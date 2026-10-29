import json
import logging
import time as relogio
from datetime import date, datetime, timedelta, timezone

from sqlalchemy.orm import Session

from src.models.assistente_interno import ConversaInterna
from src.models.contas import Usuario
from src.repositories import assistente_interno_repository as repo
from src.repositories import permissao_repository, resumo_repository, unidade_repository
from src.use_cases.conferencia_numeros import numeros_sem_origem
from src.use_cases.erros import ErroNegocio, RecursoNaoEncontrado, ServicoIndisponivel, SemPermissao
from src.use_cases.estoque import BRASILIA
from src.use_cases.ferramentas_internas import DIAS_DA_SEMANA, FERRAMENTAS, data
from src.use_cases.permissoes import marcas_visiveis
from src.utils import modelo_ia

logger = logging.getLogger(__name__)

# assistente interno (Entrega 4, ideia A): a equipe pergunta em linguagem livre e o modelo escolhe uma
# ferramenta do backend; os números vêm sempre do banco, com o login da pessoa (planejamento-interno.md)
CONVERSA_PARADA = timedelta(minutes=30)
RODADAS_DE_FERRAMENTA = 4
NOME_DA_MARCA = {"lorenzi": "Casa Lorenzi", "vulto": "Vulto"}

FORA_DO_AR = "O assistente está fora do ar agora. Tente de novo em instantes."
NAO_CONSULTOU = "Não consegui consultar agora. Tente de novo em instantes."
NAO_TERMINOU = "Não consegui terminar essa consulta. Tente perguntar uma coisa de cada vez."
SEM_NUMERO_CONFIRMADO = ("Não tenho uma consulta que confirme esse número. Pergunte de outro jeito, ou diga a "
                         "peça, a loja e o período que quer ver.")
RECUSA = "Não posso ajudar com isso por aqui."

INSTRUCOES = """Você é o assistente interno da Casa Lorenzi, uma rede de moda com duas marcas: Casa Lorenzi e \
Vulto. Você responde perguntas da equipe sobre o negócio usando só as ferramentas.

Regras:
- Todo número da resposta vem de uma ferramenta. Nunca calcule, estime, arredonde nem complete um número por \
conta própria. Se a pergunta precisa de um número que nenhuma ferramenta dá, diga que não sabe e o que você \
consegue responder.
- Copie os números exatamente como a ferramenta devolveu (R$ 1.234,56, datas dd/mm/aaaa).
- Ao oferecer outra consulta, não proponha um número (prazo, quantidade): diga "outro prazo" ou pergunte qual.
- Não deduza uma data a partir de outra (ex.: quando os pontos foram ganhos a partir do vencimento), nem quando a \
pessoa dá a regra. Se a pergunta pede algo que a resposta anterior não trouxe, consulte a ferramenta de novo.
- Diga sempre o período e onde (unidade ou marca) e, em poucas palavras, o que entrou na conta.
- Se a pergunta não diz um prazo e a ferramenta tem um padrão (ex.: 30 dias), use o padrão e diga qual usou.
- Se faltar o que a ferramenta precisa (qual loja, qual período), pergunte antes. Se o nome da loja combinar com \
mais de uma unidade da lista, pergunte qual.
- Se a ferramenta recusar por permissão, diga que a pessoa não tem acesso a esse dado, sem número nenhum.
- Você só consulta. Não altera estoque, pedidos, pontos nem pagamentos; se pedirem, diga em que tela da \
plataforma isso se faz.
- Para comparar unidades ("qual loja vendeu mais"), consulte uma vez sem unidade e use a lista por unidade.
- Unidade é loja ou CD (o tipo está na lista). Ao listar, separe lojas e CDs ("Lojas da Vulto: ...; CD: ...") e \
não chame só as lojas de "unidades".
- Pergunta fora do negócio: não responda o assunto; diga em uma frase o que você consegue consultar.
- Responda em português, curto e direto, em texto simples, sem Markdown (sem **negrito** nem #títulos; no \
máximo uma lista curta com "- ").
- As suas respostas anteriores nesta conversa já passaram pela conferência: os números delas vieram das \
ferramentas, mesmo que as chamadas não apareçam aqui. Não comente de onde vieram.

Hoje é {dia_da_semana}, {hoje} (horário de Brasília).
Marcas que esta pessoa vê: {marcas}.
Unidades que esta pessoa vê (id: nome, marca, tipo, cidade):
{unidades}"""


def _agora() -> datetime:
    return datetime.now(timezone.utc)


def _hoje() -> date:
    return _agora().astimezone(BRASILIA).date()


def _instrucoes(db: Session, usuario: Usuario, hoje: date) -> str:
    visiveis = sorted(marcas_visiveis(db, usuario))
    unidades = [u for marca in visiveis for u in resumo_repository.unidades_ativas(db, marca)]
    return INSTRUCOES.format(
        dia_da_semana=DIAS_DA_SEMANA[hoje.weekday()], hoje=data(hoje),
        marcas=", ".join(NOME_DA_MARCA[m] for m in visiveis) or "nenhuma",
        unidades="\n".join(f"- {u.id_unidade}: {u.nome}, {NOME_DA_MARCA.get(u.marca, u.marca)}, {u.tipo}, "
                           f"{u.cidade}/{u.uf}" for u in unidades) or "- nenhuma",
    )


# a conversa continua se for da pessoa e não tiver ficado 30 min parada; senão começa outra
def _conversa(db: Session, usuario: Usuario, id_conversa: int | None, agora: datetime) -> ConversaInterna:
    if id_conversa is not None:
        conversa = repo.buscar_conversa(db, id_conversa)
        if conversa is None or conversa.id_usuario != usuario.id_usuario:
            raise RecursoNaoEncontrado("Conversa não encontrada")
        if agora - conversa.ultima_msg_em <= CONVERSA_PARADA:
            return conversa
    return repo.criar_conversa(db, usuario.id_usuario, agora)


# a memória vai ao modelo só como texto, um papel por vez (a API pede usuário e assistente alternados)
def _historico(db: Session, conversa: ConversaInterna) -> list[dict]:
    mensagens: list[dict] = []
    for m in repo.memoria(db, conversa.id_conversa):
        papel = "user" if m.papel == "usuario" else "assistant"
        if mensagens and mensagens[-1]["role"] == papel:
            mensagens[-1]["content"] += "\n\n" + m.texto
        else:
            mensagens.append({"role": papel, "content": m.texto})
    while mensagens and mensagens[0]["role"] != "user":
        mensagens.pop(0)
    return mensagens


def _executar(db: Session, usuario: Usuario, nome: str, parametros: dict, hoje: date) -> tuple[str, dict]:
    ferramenta = FERRAMENTAS.get(nome)
    if ferramenta is None:
        return "invalido", {"erro": f"Ferramenta desconhecida: {nome}"}
    try:
        resultado = ferramenta.executar(db, usuario, parametros, hoje)
    except SemPermissao as erro:
        return "sem_permissao", {"erro": erro.mensagem, "sem_permissao": True}
    except ErroNegocio as erro:
        return "invalido", {"erro": erro.mensagem}
    # cada ferramenta diz se não achou nada (busca vazia, período sem venda): não é erro
    return ("vazio" if resultado.get("vazio") else "dado"), resultado


class _FalhaDeFerramenta(Exception):
    pass


# a unidade escolhida no topo da plataforma, se existe e é de uma marca que a pessoa vê (senão, como "todas")
def _unidade_em_foco(db: Session, usuario: Usuario, id_unidade: int | None):
    if id_unidade is None:
        return None
    unidade = unidade_repository.buscar_unidade(db, id_unidade)
    if unidade is None or unidade.marca not in marcas_visiveis(db, usuario):
        return None
    return unidade


# o contexto da tela vai junto da pergunta (e não nas instruções, que ficam em cache): sem unidade na pergunta,
# vale a do topo, e a resposta diz que usou essa
# a ordem: o que a pergunta diz; senão, a unidade de que a conversa vinha falando ("e há mais de 6 meses?" segue na
# mesma loja); senão, a do topo
def _com_contexto_da_tela(texto: str, unidade) -> str:
    if unidade is None:
        return (f"{texto}\n\n[Tela: \"Todas as unidades\" selecionado no topo. Se a pergunta não disser a "
                "unidade e for continuação de uma pergunta sobre uma unidade, siga nela; senão, responda da rede "
                "(as marcas que a pessoa vê).]")
    return (f"{texto}\n\n[Tela: unidade {unidade.nome} (id {unidade.id_unidade}) selecionada no topo. Se a "
            "pergunta não disser a unidade nem pedir a rede ou uma marca, siga na unidade de que a conversa vinha "
            "falando; se não houver, use esta e diga que é a unidade selecionada no topo.]")


def responder(db: Session, usuario: Usuario, texto: str, id_conversa: int | None = None,
              id_unidade_em_foco: int | None = None) -> dict:
    if usuario.tipo_conta != "interna":
        raise SemPermissao("Só para contas internas")
    agora, hoje, inicio = _agora(), _hoje(), relogio.monotonic()
    conversa = _conversa(db, usuario, id_conversa, agora)
    mensagens = _historico(db, conversa)
    # as perguntas e respostas anteriores já passaram pela conferência: os números delas valem nesta resposta
    ja_conferido = "\n".join(m["content"] for m in mensagens)
    instrucoes = _instrucoes(db, usuario, hoje)
    em_foco = _unidade_em_foco(db, usuario, id_unidade_em_foco)
    conversa.ultima_msg_em = agora
    repo.adicionar_mensagem(db, conversa, papel="usuario", texto=texto)
    # a pergunta fica gravada antes de chamar o modelo: se algo falhar no meio, ela não se perde
    db.commit()

    mensagens.append({"role": "user", "content": _com_contexto_da_tela(texto, em_foco)})
    definicoes = [f.definicao() for f in FERRAMENTAS.values()]
    resultados: list[dict] = []
    recusas: list[str] = []  # motivo de cada recusa por permissão, escrito pelo backend
    usadas: list[str] = []
    tokens = {"tokens_entrada": 0, "tokens_saida": 0, "tokens_cache_lidos": 0, "tokens_cache_escritos": 0}
    modelo, resposta, conferidos = None, None, None
    try:
        # cada rodada: o modelo pede ferramentas ou responde. Depois do limite, para sem executar mais nada
        for numero_da_rodada in range(RODADAS_DE_FERRAMENTA + 1):
            rodada = modelo_ia.conversar(instrucoes, mensagens, definicoes)
            modelo = rodada.modelo
            for chave in tokens:
                tokens[chave] += getattr(rodada, chave)
            if rodada.parou_por == "refusal":
                resposta = RECUSA
                break
            # max_tokens com ferramenta pedida: os parâmetros podem ter vindo cortados, então não executa
            if not rodada.chamadas or rodada.parou_por == "max_tokens":
                resposta = rodada.texto or None
                break
            if numero_da_rodada == RODADAS_DE_FERRAMENTA:
                break
            mensagens.append({"role": "assistant", "content": rodada.conteudo})
            respostas_das_ferramentas = []
            for chamada in rodada.chamadas:
                try:
                    situacao, resultado = _executar(db, usuario, chamada.nome, chamada.parametros, hoje)
                except Exception as erro:  # erro de verdade (banco fora do ar): responde sem passar pelo modelo
                    db.rollback()
                    logger.exception("ferramenta %s falhou", chamada.nome)
                    repo.adicionar_mensagem(db, conversa, papel="ferramenta", ferramenta=chamada.nome,
                                            parametros=chamada.parametros, resultado="falhou")
                    raise _FalhaDeFerramenta from erro
                usadas.append(chamada.nome)
                repo.adicionar_mensagem(db, conversa, papel="ferramenta", ferramenta=chamada.nome,
                                        parametros=chamada.parametros, resultado=situacao,
                                        texto=json.dumps(resultado, ensure_ascii=False, default=str))
                if situacao in ("dado", "vazio"):
                    resultados.append(resultado)
                elif situacao == "sem_permissao" and resultado["erro"] not in recusas:
                    recusas.append(resultado["erro"])
                para_o_modelo = {k: v for k, v in resultado.items() if k != "links"}
                respostas_das_ferramentas.append({
                    "type": "tool_result", "tool_use_id": chamada.id,
                    "content": json.dumps(para_o_modelo, ensure_ascii=False, default=str),
                    **({"is_error": True} if "erro" in resultado else {}),
                })
            mensagens.append({"role": "user", "content": respostas_das_ferramentas})
    except _FalhaDeFerramenta:
        resposta = NAO_CONSULTOU
    except modelo_ia.ErroModelo:
        repo.adicionar_mensagem(db, conversa, papel="assistente", texto=FORA_DO_AR, duracao_ms=_ms(inicio))
        db.commit()
        raise ServicoIndisponivel(FORA_DO_AR)

    if resposta is None:
        resposta = NAO_TERMINOU
    elif resposta not in (NAO_CONSULTOU, NAO_TERMINOU, RECUSA):
        # trava da CFO: número que não veio das ferramentas não chega à pessoa
        sem_origem = numeros_sem_origem(resposta, resultados, texto, f"{instrucoes}\n{ja_conferido}", hoje)
        conferidos = not sem_origem
        if sem_origem:
            # só os números barrados, sem o texto (que pode trazer dado de cliente)
            logger.warning("conversa %s: números sem origem na resposta do modelo: %s", conversa.id_conversa,
                           ", ".join(sorted(sem_origem)))
            # o texto fixo das ferramentas; sem dado, o motivo da recusa (não revela nada além do que a pessoa
            # já sabe da própria permissão); sem nenhum dos dois, a resposta genérica
            resposta = ("\n".join(r["texto"] for r in resultados) or "\n".join(f"{m}." for m in recusas)
                        or SEM_NUMERO_CONFIRMADO)

    repo.adicionar_mensagem(db, conversa, papel="assistente", texto=resposta, modelo=modelo,
                            numeros_conferidos=conferidos, duracao_ms=_ms(inicio), **tokens)
    db.commit()
    links = list({link["url"]: link for r in resultados for link in r.get("links", [])}.values())
    return {"id_conversa": conversa.id_conversa, "resposta": resposta, "links": links,
            "ferramentas": usadas, "numeros_conferidos": conferidos,
            "unidade_em_foco": {"id_unidade": em_foco.id_unidade, "nome": em_foco.nome} if em_foco else None}


def _ms(inicio: float) -> int:
    return int((relogio.monotonic() - inicio) * 1000)


# a pessoa vê as próprias conversas; o Admin vê todas, para revisar onde o assistente errou
def ver_conversa(db: Session, usuario: Usuario, id_conversa: int) -> dict:
    conversa = repo.buscar_conversa(db, id_conversa)
    eh_admin = usuario.tipo_conta == "interna" and permissao_repository.modelo_eh_admin(db, usuario.id_modelo_acesso)
    if conversa is None or (conversa.id_usuario != usuario.id_usuario and not eh_admin):
        raise RecursoNaoEncontrado("Conversa não encontrada")
    return {
        "id_conversa": conversa.id_conversa, "iniciada_em": conversa.iniciada_em, "avaliacao": conversa.avaliacao,
        "mensagens": [{"papel": m.papel, "texto": m.texto, "criado_em": m.criado_em}
                      for m in repo.mensagens_da_conversa(db, id_conversa)],
    }


def avaliar(db: Session, usuario: Usuario, id_conversa: int, avaliacao: str) -> None:
    conversa = repo.buscar_conversa(db, id_conversa)
    if conversa is None or conversa.id_usuario != usuario.id_usuario:
        raise RecursoNaoEncontrado("Conversa não encontrada")
    conversa.avaliacao = avaliacao
    db.commit()
