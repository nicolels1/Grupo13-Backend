import hashlib
import hmac
import logging
import re
import secrets
import uuid
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from fastapi.encoders import jsonable_encoder
from sqlalchemy.orm import Session

from src.config import settings
from src.models.assistente import (
    AfirmacaoAssistente, AvisoPontosVencendo, CodigoVerificacao, ConversaCanal, MensagemConversa,
)
from src.models.estoque import Unidade
from src.repositories import assistente_repository as repo
from src.repositories import atendimento_repository, cliente_sem_conta_repository, fidelidade_repository
from src.repositories import pedido_repository, usuario_repository
from src.use_cases import atendimento, devolucoes, fidelidade, resumo_conversa
from src.use_cases.assistente import endereco
from src.use_cases.erros import Conflito, MuitasTentativas, NaoAutenticado, RecursoNaoEncontrado, RegraDeNegocio
from src.use_cases.horarios import BRASILIA, DIAS_DA_SEMANA, feriado_nacional, formatar_hora
from src.utils.cpf import cpf_valido, normalizar_cpf
from src.utils.email import ErroEmail, enviar_email

logger = logging.getLogger(__name__)

# ferramentas do assistente do cliente que dependem de saber quem é o cliente. A conversa (canal + telefone
# ou sessão) vem do n8n, que a tira do canal, nunca do modelo; a conta só fica ligada a ela depois do
# código mandado ao e-mail do cadastro, e só enquanto a conversa durar

DURACAO_DA_CONVERSA = timedelta(minutes=30)  # sem mensagem por esse tempo, a conversa termina
VALIDADE_DO_CODIGO = timedelta(minutes=10)
ERROS_ATE_BLOQUEAR = 5
BLOQUEIO = timedelta(minutes=30)
CODIGOS_POR_DIA = 3  # contra quem testa quais CPFs têm cadastro
MENSAGENS_POR_DIA = 100
PEDIDOS_NA_CONVERSA = 5
DIAS_PARA_RETIRAR = 7  # depois disso o pg_cron cancela (migration 03aeb347f6cf)

# a mesma resposta para CPF com e sem cadastro: não revela quem é cliente
RESPOSTA_DO_CPF = (
    "Se esse CPF tiver cadastro, mandamos um código de 6 números para o e-mail cadastrado. "
    "Ele vale por 10 minutos."
)
ASSUNTO_DO_EMAIL = "Seu código de atendimento da Casa Lorenzi"
SEM_PEDIDOS_NO_SITE ="Não encontrei pedidos do site no seu cadastro."

PROXIMA_CATEGORIA = {"bronze": ("prata", "limite_prata"), "prata": ("ouro", "limite_ouro")}

# horário da equipe de atendimento, exemplo do case igual ao das lojas de rua; domingo e feriado nacional
# fechado. Fora dele, o assistente diz quando a equipe volta, em vez de deixar o cliente sem resposta
HORARIO_DA_EQUIPE = {dia: (time(10), time(20)) for dia in range(5)} | {5: (time(10), time(18))}
HORARIO_DA_EQUIPE_EM_TEXTO = "de segunda a sexta das 10h às 20h e aos sábados das 10h às 18h"
DIAS_PARA_ACHAR_A_VOLTA = 14  # folga para qualquer sequência de domingo e feriados

# CPF (com ou sem pontos) e o código de 6 números não ficam no texto guardado da conversa
CPF_NO_TEXTO = re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b")
CODIGO_NO_TEXTO = re.compile(r"\b\d{6}\b")


# ---------- texto pronto ----------

def _numero(valor: int) -> str:
    return f"{valor:,}".replace(",", ".")


def _reais(valor: Decimal) -> str:
    return "R$ " + f"{valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _data(valor: date) -> str:
    return valor.strftime("%d/%m/%Y")


def _pontos(valor: int) -> str:
    return f"{_numero(valor)} {'ponto' if abs(valor) == 1 else 'pontos'}"


def _primeiro_nome(nome: str | None) -> str | None:
    return nome.split()[0] if nome else None


# ---------- horário da equipe ----------

def _intervalo_da_equipe(dia: date) -> tuple[time, time] | None:
    return None if feriado_nacional(dia) else HORARIO_DA_EQUIPE.get(dia.weekday())


# quando a equipe volta a atender; vazio enquanto ela está atendendo
def equipe_volta_em(momento: datetime) -> datetime | None:
    agora = momento.astimezone(BRASILIA)
    for n in range(DIAS_PARA_ACHAR_A_VOLTA):
        dia = agora.date() + timedelta(days=n)
        intervalo = _intervalo_da_equipe(dia)
        if intervalo is None:
            continue
        abre, fecha = (datetime.combine(dia, hora, BRASILIA) for hora in intervalo)
        if agora < fecha:
            return None if agora >= abre else abre
    return None


def _quando_volta(volta: datetime, momento: datetime) -> str:
    hoje = momento.astimezone(BRASILIA).date()
    hora = formatar_hora(volta.time())
    if volta.date() == hoje:
        return f"hoje às {hora}"
    if volta.date() == hoje + timedelta(days=1):
        return f"amanhã às {hora}"
    return f"{DIAS_DA_SEMANA[volta.weekday()]} ({volta.strftime('%d/%m')}) às {hora}"


def _aviso_da_equipe(volta: datetime | None, momento: datetime) -> str | None:
    if volta is None:
        return None
    return (f"A nossa equipe atende {HORARIO_DA_EQUIPE_EM_TEXTO}. Ela volta {_quando_volta(volta, momento)} "
            "e responde por aqui.")


# ---------- estado da conversa ----------

def _confirmado(conversa: ConversaCanal, momento: datetime) -> bool:
    tem_dono = conversa.id_cliente is not None or conversa.id_cliente_sem_conta is not None
    return tem_dono and conversa.confirmado_ate is not None and conversa.confirmado_ate > momento


def _desconfirmar(conversa: ConversaCanal) -> None:
    conversa.id_cliente = None
    conversa.id_cliente_sem_conta = None
    conversa.confirmado_ate = None


def _bloqueado_ate(conversa: ConversaCanal, momento: datetime) -> datetime | None:
    return conversa.bloqueado_ate if conversa.bloqueado_ate and conversa.bloqueado_ate > momento else None


def _estado(conversa: ConversaCanal, momento: datetime, conversa_nova: bool = False) -> dict:
    volta = equipe_volta_em(momento)
    return {
        "etapa": conversa.etapa,
        "confirmado": _confirmado(conversa, momento),
        "conversa_nova": conversa_nova,
        "limite_atingido": conversa.msgs_hoje > MENSAGENS_POR_DIA,
        # o texto fixo do limite sai uma vez só; depois, silêncio até o dia seguinte
        "avisar_limite": conversa.msgs_hoje == MENSAGENS_POR_DIA + 1,
        "bloqueado_ate": _bloqueado_ate(conversa, momento),
        "id_chamado": conversa.id_chamado,
        "equipe_disponivel": volta is None,
        "equipe_volta_em": volta,
        "aviso_equipe": _aviso_da_equipe(volta, momento),
    }


# fora da confirmação por e-mail, a conversa volta para a IA; com chamado aberto, a mensagem também vai
# para ele (o cliente pode pedir os pontos no meio do chamado sem que o chamado se perca)
def _etapa_sem_verificacao(conversa: ConversaCanal) -> str:
    return "em_chamado" if conversa.id_chamado is not None else "livre"


# chamado concluído pela equipe: o assistente volta a atender a conversa sozinho
def _soltar_chamado_concluido(db: Session, conversa: ConversaCanal) -> None:
    if conversa.id_chamado is None:
        return
    chamado = atendimento_repository.buscar_chamado(db, conversa.id_chamado)
    if chamado is None or chamado.status == "concluido":
        conversa.id_chamado = None
        if conversa.etapa == "em_chamado":
            conversa.etapa = "livre"


def _conversa(db: Session, canal: str, id_externo: str, travar: bool = False) -> ConversaCanal:
    conversa = repo.buscar_conversa(db, canal, id_externo, travar)
    if conversa is None:
        raise RecursoNaoEncontrado("Conversa não encontrada: registre a mensagem antes")
    return conversa


# chamada pelo n8n no começo de toda mensagem do cliente. Passados 30 min da anterior, a conversa é
# nova: a conta deixa de estar confirmada e o n8n apaga a memória curta (conversa_nova)
def registrar_mensagem(db: Session, canal: str, id_externo: str, momento: datetime) -> dict:
    conversa = repo.garantir_conversa(db, canal, id_externo)
    anterior = conversa.ultima_msg_cliente_em
    conversa_nova = anterior is None or momento - anterior > DURACAO_DA_CONVERSA
    if conversa_nova:
        _desconfirmar(conversa)
        # o rascunho do chamado também vence: era de quem estava confirmado na conversa anterior
        conversa.rascunho_chamado = None
        if conversa.etapa in ("esperando_cpf", "esperando_codigo", "confirmando_chamado"):
            conversa.etapa = _etapa_sem_verificacao(conversa)
    _soltar_chamado_concluido(db, conversa)
    # cada conversa tem o seu resumo anônimo; a conversa aberta antes do resumo existir também ganha um
    if conversa_nova or conversa.id_resumo_atual is None:
        resumo_conversa.abrir(db, conversa, momento)
    if conversa_nova or conversa.conversa_iniciada_em is None:
        conversa.conversa_iniciada_em = momento
    hoje = momento.astimezone(BRASILIA).date()
    if conversa.dia_contagem != hoje:
        conversa.dia_contagem = hoje
        conversa.msgs_hoje = 0
    conversa.msgs_hoje += 1
    conversa.ultima_msg_cliente_em = momento
    if _confirmado(conversa, momento):
        conversa.confirmado_ate = momento + DURACAO_DA_CONVERSA
    db.commit()
    return _estado(conversa, momento, conversa_nova)


# o n8n só põe a conversa esperando o CPF (o cliente pediu pontos ou pedidos) ou a devolve para a IA
# (desistiu, ou apertou [Corrigir] no resumo do chamado); esperando_codigo e a confirmação saem das rotas de
# verificação, e confirmando_chamado sai do rascunho do chamado
def mudar_etapa(db: Session, canal: str, id_externo: str, etapa: str, momento: datetime) -> dict:
    conversa = _conversa(db, canal, id_externo, travar=True)
    conversa.rascunho_chamado = None
    if etapa == "esperando_cpf" and _confirmado(conversa, momento):
        etapa = _etapa_sem_verificacao(conversa)
    if etapa == "livre":
        repo.encerrar_codigos(db, canal, id_externo, momento)
        etapa = _etapa_sem_verificacao(conversa)
    conversa.etapa = etapa
    db.commit()
    return _estado(conversa, momento)


# ---------- CPF e código por e-mail ----------

def _hash_do_codigo(codigo: str) -> str:
    return hmac.new(settings.ASSISTENTE_CHAVE.encode(), codigo.encode(), hashlib.sha256).hexdigest()


def _conferir_bloqueio(conversa: ConversaCanal, momento: datetime) -> None:
    bloqueio = _bloqueado_ate(conversa, momento)
    if bloqueio:
        hora = bloqueio.astimezone(BRASILIA).strftime("%H:%M")
        raise MuitasTentativas(f"Por segurança, a confirmação está pausada. Tente de novo depois das {hora}.")


# quem tem o CPF e um e-mail para receber o código: a conta de cliente ou o cliente sem conta da
# migração (ADR 0016). Conta inativa e cadastro sem e-mail não confirmam
def _dono_do_cpf(db: Session, cpf: str) -> dict | None:
    conta = usuario_repository.buscar_cliente_por_cpf(db, cpf)
    if conta is not None:
        if conta.status_conta == "inativa":
            return None
        return {"id_cliente": conta.id_usuario, "id_cliente_sem_conta": None, "email": conta.email, "nome": conta.nome}
    sem_conta = cliente_sem_conta_repository.buscar_por_cpf(db, cpf)
    if sem_conta is not None and sem_conta.email:
        return {"id_cliente": None, "id_cliente_sem_conta": sem_conta.id_cliente_sem_conta,
                "email": sem_conta.email, "nome": sem_conta.nome}
    return None


def _texto_do_email(nome: str | None, codigo: str) -> str:
    saudacao = f"Olá, {_primeiro_nome(nome)}!" if nome else "Olá!"
    return (
        f"{saudacao}\n\n"
        f"Seu código para confirmar quem é você no atendimento da Casa Lorenzi é: {codigo}\n\n"
        "Ele vale por 10 minutos. Não passe este código para ninguém: a nossa equipe nunca pede.\n"
        "Se não foi você que pediu, ignore este e-mail. Sem o código, ninguém vê seus pontos nem seus pedidos.\n"
    )


# o CPF não é segredo, então só ele não confirma: o código vai para o e-mail do cadastro. CPF sem
# cadastro também gera um código (que nenhum número acerta), e a resposta é sempre a mesma
def pedir_codigo(db: Session, canal: str, id_externo: str, cpf: str, momento: datetime) -> dict:
    conversa = _conversa(db, canal, id_externo, travar=True)
    _conferir_bloqueio(conversa, momento)
    cpf = normalizar_cpf(cpf)
    if not cpf_valido(cpf):
        raise RegraDeNegocio("CPF inválido. Confira os 11 números.")
    inicio_do_dia = datetime.combine(momento.astimezone(BRASILIA).date(), time.min, BRASILIA)
    if repo.codigos_desde(db, canal, id_externo, inicio_do_dia) >= CODIGOS_POR_DIA:
        raise MuitasTentativas("Já mandamos 3 códigos hoje nesta conversa. Tente de novo amanhã.")

    dono = _dono_do_cpf(db, cpf)
    codigo = f"{secrets.randbelow(1_000_000):06d}"
    repo.encerrar_codigos(db, canal, id_externo, momento)
    db.add(CodigoVerificacao(
        canal=canal, id_externo=id_externo,
        id_cliente=dono["id_cliente"] if dono else None,
        id_cliente_sem_conta=dono["id_cliente_sem_conta"] if dono else None,
        codigo_hash=_hash_do_codigo(codigo), expira_em=momento + VALIDADE_DO_CODIGO, tentativas=0, criado_em=momento,
    ))
    conversa.rascunho_chamado = None  # pedir o CPF no meio da confirmação do chamado desfaz o rascunho
    conversa.etapa = "esperando_codigo"
    db.commit()

    if dono is not None:
        try:
            enviar_email(dono["email"], ASSUNTO_DO_EMAIL, _texto_do_email(dono["nome"], codigo))
        except ErroEmail:
            # a resposta continua a mesma, para não revelar que o CPF tem cadastro; o erro fica no log
            logger.error("código do assistente não enviado (canal=%s)", canal)
    return {"mensagem": RESPOSTA_DO_CPF, "validade_minutos": int(VALIDADE_DO_CODIGO.total_seconds() // 60)}


def conferir_codigo(db: Session, canal: str, id_externo: str, codigo: str, momento: datetime) -> dict:
    conversa = _conversa(db, canal, id_externo, travar=True)
    _conferir_bloqueio(conversa, momento)
    aberto = repo.codigo_aberto(db, canal, id_externo, momento)
    if aberto is None:
        if conversa.etapa == "esperando_codigo":
            conversa.etapa = "esperando_cpf"
            db.commit()
        raise RegraDeNegocio("Esse código expirou. Mande o CPF de novo para receber outro.")

    tem_dono = aberto.id_cliente is not None or aberto.id_cliente_sem_conta is not None
    certo = tem_dono and hmac.compare_digest(aberto.codigo_hash, _hash_do_codigo(codigo.strip()))
    if not certo:
        aberto.tentativas += 1
        if aberto.tentativas >= ERROS_ATE_BLOQUEAR:
            aberto.encerrado_em = momento
            conversa.bloqueado_ate = momento + BLOQUEIO
            conversa.etapa = _etapa_sem_verificacao(conversa)
            db.commit()
            _conferir_bloqueio(conversa, momento)  # avisa até quando ficou pausado
        db.commit()
        restam = ERROS_ATE_BLOQUEAR - aberto.tentativas
        tentativas = "Resta 1 tentativa" if restam == 1 else f"Restam {restam} tentativas"
        raise RegraDeNegocio(f"Código incorreto. {tentativas}.")

    aberto.encerrado_em = momento
    conversa.id_cliente = aberto.id_cliente
    conversa.id_cliente_sem_conta = aberto.id_cliente_sem_conta
    conversa.confirmado_ate = momento + DURACAO_DA_CONVERSA
    conversa.etapa = _etapa_sem_verificacao(conversa)
    resumo_conversa.marcar_autenticou(db, conversa)
    # o texto anônimo desta conversa passa para a conta (como a conversa do cliente sem conta, ADR 0016)
    if conversa.conversa_iniciada_em is not None:
        repo.passar_texto_para_a_conta(db, canal, id_externo, conversa.conversa_iniciada_em,
                                       aberto.id_cliente, aberto.id_cliente_sem_conta)
    if aberto.id_cliente is not None:
        nome = usuario_repository.buscar_por_id(db, aberto.id_cliente).nome
    else:
        nome = cliente_sem_conta_repository.buscar(db, aberto.id_cliente_sem_conta).nome
    db.commit()
    return {"confirmado": True, "nome": _primeiro_nome(nome)}


# ---------- pontos e pedidos ----------

# só com a conta confirmada nesta conversa; sem ela, o n8n pede o CPF
def _conversa_confirmada(db: Session, canal: str, id_externo: str, momento: datetime) -> ConversaCanal:
    conversa = repo.buscar_conversa(db, canal, id_externo)
    if conversa is None or not _confirmado(conversa, momento):
        raise NaoAutenticado("Confirme quem é você antes: peça o CPF")
    return conversa


# auditoria: o que foi entregue ao assistente com dado pessoal, na mesma transação da resposta
def _afirmar(db: Session, conversa: ConversaCanal, ferramenta: str, dados: dict, momento: datetime) -> None:
    db.add(AfirmacaoAssistente(
        canal=conversa.canal, id_externo=conversa.id_externo, id_cliente=conversa.id_cliente,
        id_cliente_sem_conta=conversa.id_cliente_sem_conta, ferramenta=ferramenta,
        dados=jsonable_encoder(dados), criado_em=momento,
    ))
    db.commit()


# o que falta para subir: limite da regra vigente menos os pontos ganhos no ano (a categoria sobe pelo
# que se ganha no ano, não pelo saldo). Ouro não tem próxima
def _proxima_categoria(db: Session, saldo: dict) -> tuple[str | None, int | None]:
    if saldo["categoria"] not in PROXIMA_CATEGORIA:
        return None, None
    proxima, limite = PROXIMA_CATEGORIA[saldo["categoria"]]
    regra = fidelidade_repository.regra_vigente(db, saldo["marca"], fidelidade.hoje())
    if regra is None:
        return None, None
    return proxima, max(getattr(regra, limite) - saldo["ganhos_no_ano"], 0)


def _texto_da_marca(marca: dict) -> str:
    partes = [f"{marca['marca'].capitalize()}: {_pontos(marca['saldo'])} ({_reais(marca['valor_em_reais'])}), "
              f"categoria {marca['categoria'].capitalize()}."]
    proximo, sem_data = marca["proximo_vencimento"], marca["pontos_sem_vencimento"]
    if marca["saldo"] > 0:
        if proximo and sem_data:
            partes.append(f"{_pontos(proximo['pontos'])} vencem em {_data(proximo['data'])} e "
                          f"{_numero(sem_data)} ainda não têm data de vencimento.")
        elif proximo and proximo["pontos"] == marca["saldo"]:
            partes.append(f"Vencem em {_data(proximo['data'])}.")
        elif proximo:
            partes.append(f"Os próximos {_pontos(proximo['pontos'])} vencem em {_data(proximo['data'])}.")
        elif sem_data:
            partes.append("Ainda não têm data de vencimento.")
    if marca["proxima_categoria"] and marca["faltam_para_subir"]:
        partes.append(f"Faltam {_pontos(marca['faltam_para_subir'])} ganhos este ano para chegar a "
                      f"{marca['proxima_categoria'].capitalize()}.")
    return " ".join(partes)


# por marca, nunca somados (valor do ponto e categoria são diferentes). Os números vêm prontos daqui;
# o texto é o que o n8n manda se a resposta da IA trouxer um número diferente destes
def meus_pontos(db: Session, canal: str, id_externo: str, momento: datetime) -> dict:
    conversa = _conversa_confirmada(db, canal, id_externo, momento)
    dono = (conversa.id_cliente, conversa.id_cliente_sem_conta)
    sem_data = repo.pontos_sem_vencimento(db, *dono)
    marcas = []
    for saldo in fidelidade_repository.resumo(db, *dono):
        proxima, faltam = _proxima_categoria(db, saldo)
        vencimento = saldo["proximo_vencimento"]
        marca = {
            "marca": saldo["marca"],
            "saldo": saldo["saldo"],
            "valor_em_reais": saldo["valor_em_reais"],
            "categoria": saldo["categoria"],
            "proxima_categoria": proxima,
            "faltam_para_subir": faltam,
            "proximo_vencimento": {"data": vencimento.astimezone(BRASILIA).date(),
                                   "pontos": saldo["pontos_no_proximo_vencimento"]} if vencimento else None,
            "pontos_sem_vencimento": sem_data.get(saldo["marca"], 0),
        }
        marca["texto"] = _texto_da_marca(marca)
        marcas.append(marca)
    texto = "\n".join(m["texto"] for m in marcas) or "Você ainda não tem pontos na Lorenzi nem na Vulto."
    resultado = {"marcas": marcas, "texto": texto}
    _afirmar(db, conversa, "meus_pontos", resultado, momento)
    resultado["oferecer_lembrete"] = _oferecer_lembrete(db, conversa, marcas, momento)
    return resultado


# o lembrete por e-mail antes de os pontos vencerem é oferecido uma vez por conta, a quem tem pontos e ainda
# não aceitou; fica marcado já aqui, quando o assistente recebe o aviso para oferecer. Cliente sem conta da
# migração não recebe lembrete até criar a conta
def _oferecer_lembrete(db: Session, conversa: ConversaCanal, marcas: list[dict], momento: datetime) -> bool:
    if conversa.id_cliente is None or not any(m["saldo"] > 0 for m in marcas):
        return False
    cliente = usuario_repository.buscar_por_id(db, conversa.id_cliente)
    if cliente.lembrete_pontos_aceito_em is not None or cliente.lembrete_pontos_oferecido_em is not None:
        return False
    cliente.lembrete_pontos_oferecido_em = momento
    db.commit()
    return True


# o cliente aceitou ou recusou o lembrete na conversa; recusar também desliga um aceite anterior
def responder_lembrete(db: Session, canal: str, id_externo: str, aceita: bool, momento: datetime) -> dict:
    conversa = _conversa_confirmada(db, canal, id_externo, momento)
    if conversa.id_cliente is None:
        raise RegraDeNegocio(f"O lembrete de pontos é para quem tem conta no site. Crie a sua em "
                             f"{settings.SITE_URL}/cadastro e depois me chame de novo por aqui.")
    cliente = usuario_repository.buscar_por_id(db, conversa.id_cliente)
    cliente.lembrete_pontos_oferecido_em = cliente.lembrete_pontos_oferecido_em or momento
    if aceita:
        cliente.lembrete_pontos_aceito_em = cliente.lembrete_pontos_aceito_em or momento
        mensagem = ("Combinado! Vou te avisar por e-mail antes de os seus pontos vencerem. "
                    "Se quiser parar, é só me pedir por aqui.")
    else:
        cliente.lembrete_pontos_aceito_em = None
        mensagem = "Tudo bem, não vou mandar o lembrete. Se mudar de ideia, é só me pedir por aqui."
    db.commit()
    return {"aceita": aceita, "mensagem": mensagem}


# ---------- aviso de pontos vencendo (e-mail, sem IA) ----------

ANTECEDENCIA_DO_AVISO = timedelta(days=14)
ASSUNTO_DO_AVISO = "Seus pontos vão vencer"


# assinatura do link "parar de receber": quem tem o link desliga o lembrete daquela conta, sem login
def _assinatura_do_lembrete(id_cliente: uuid.UUID) -> str:
    texto = f"parar-lembrete-pontos:{id_cliente}".encode()
    return hmac.new(settings.ASSISTENTE_CHAVE.encode(), texto, hashlib.sha256).hexdigest()


def link_para_parar_o_lembrete(id_cliente: uuid.UUID) -> str:
    return f"{settings.API_URL}/lembrete-pontos/parar?token={id_cliente}.{_assinatura_do_lembrete(id_cliente)}"


# os lotes do e-mail somados por marca e data: o cliente vê "320 pontos vencem em 12/11", não um lote por compra
def _texto_do_aviso(cliente, lotes: list) -> str:
    somados = defaultdict(lambda: [0, Decimal(0)])
    for lote in lotes:
        soma = somados[(lote.marca, lote.vence_em.astimezone(BRASILIA).date())]
        soma[0] += lote.restante
        soma[1] += lote.restante * lote.valor_ponto
    linhas = [f"- {marca.capitalize()}: {_pontos(pontos)} ({_reais(valor)}) vencem em {_data(dia)}."
              for (marca, dia), (pontos, valor) in sorted(somados.items(), key=lambda item: item[0][1])]
    saudacao = f"Olá, {_primeiro_nome(cliente.nome)}!" if cliente.nome else "Olá!"
    return (
        f"{saudacao}\n\nAlguns dos seus pontos vão vencer:\n" + "\n".join(linhas) + "\n\n"
        "Use os pontos em compras no site ou nas lojas antes dessas datas.\n\n"
        f"Para não receber mais este aviso: {link_para_parar_o_lembrete(cliente.id_usuario)}\n"
    )


# chamado uma vez por dia pelo pg_cron: um e-mail por cliente com os lotes que vencem nos próximos 14 dias e
# ainda não foram avisados. Se o e-mail falhar, os lotes não ficam marcados e a tentativa volta no dia seguinte
def avisar_pontos_vencendo(db: Session, momento: datetime) -> dict:
    por_cliente = defaultdict(list)
    clientes = {}
    for lote, cliente in repo.lotes_para_avisar(db, momento, momento + ANTECEDENCIA_DO_AVISO):
        por_cliente[cliente.id_usuario].append(lote)
        clientes[cliente.id_usuario] = cliente
    avisados = 0
    for id_cliente, lotes in por_cliente.items():
        try:
            enviar_email(clientes[id_cliente].email, ASSUNTO_DO_AVISO, _texto_do_aviso(clientes[id_cliente], lotes))
        except ErroEmail:
            logger.error("aviso de pontos vencendo não enviado; tenta de novo amanhã")
            continue
        db.add_all([AvisoPontosVencendo(id_movimento=lote.id_movimento, avisado_em=momento) for lote in lotes])
        db.commit()
        avisados += 1
    return {"clientes_avisados": avisados, "clientes_com_falha": len(por_cliente) - avisados}


# o link do e-mail: assinatura certa desliga o lembrete (de novo, se já estava desligado, não muda nada)
def parar_lembrete_pelo_link(db: Session, token: str) -> bool:
    id_texto, _, assinatura = token.partition(".")
    try:
        id_cliente = uuid.UUID(id_texto)
    except ValueError:
        return False
    if not settings.ASSISTENTE_CHAVE or not hmac.compare_digest(assinatura, _assinatura_do_lembrete(id_cliente)):
        return False
    cliente = usuario_repository.buscar_por_id(db, id_cliente)
    if cliente is None:
        return False
    cliente.lembrete_pontos_aceito_em = None
    db.commit()
    return True


STATUS_DO_PEDIDO = {
    "aguardando_pagamento": "aguardando pagamento",
    "pago": "pago, em preparação",
    "enviado": "enviado",
    "pronto_para_retirada": "pronto para retirar",
    "entregue": "entregue",
    "cancelado": "cancelado",
}
MOTIVO_DO_CANCELAMENTO = {
    "cliente": "cancelado por você",
    "reserva_vencida": "cancelado porque o pagamento não foi feito a tempo",
    "retirada_vencida": f"cancelado porque não foi retirado em {DIAS_PARA_RETIRAR} dias",
    "equipe": "cancelado pela nossa equipe",
}
NOME_DO_METODO = {
    "pix": "Pix", "cartao_credito": "cartão de crédito", "cartao_debito": "cartão de débito",
    "dinheiro": "dinheiro", "nao_informado": "meio não informado",
}
STATUS_DO_ESTORNO = {"pendente": "em andamento", "aprovado": "feito", "recusado": "recusado"}


# o último dia para trocar ou devolver, pela mesma regra do balcão (o migrado sem entrega conta do pagamento).
# Só no pedido entregue: antes da entrega ainda não há prazo
def _troca_ate(db: Session, pedido) -> date | None:
    if pedido.status != "entregue":
        return None
    try:
        inicio = devolucoes._inicio_do_prazo(db, pedido)
    except RegraDeNegocio:
        return None
    return (inicio + devolucoes.PRAZO).astimezone(BRASILIA).date() if inicio else None


def _montar_pagamento(lancamento) -> dict:
    return {"tipo": lancamento.tipo, "metodo": lancamento.metodo, "valor": lancamento.valor,
            "status": lancamento.status, "em": lancamento.criado_em.astimezone(BRASILIA).date()}


def _montar_pedido(pedido, unidade: Unidade, itens: list, lancamentos: list, troca_ate: date | None,
                   hoje: date) -> dict:
    retirada = None
    if pedido.modalidade == "retirada" and pedido.status in ("pago", "pronto_para_retirada"):
        retirar_ate = None
        if pedido.status == "pronto_para_retirada" and pedido.pronto_retirada_em:
            retirar_ate = (pedido.pronto_retirada_em + timedelta(days=DIAS_PARA_RETIRAR)).astimezone(BRASILIA).date()
        retirada = {"loja": unidade.nome, "endereco": endereco(unidade), "retirar_ate": retirar_ate}
    pagar_ate = None
    if pedido.status == "aguardando_pagamento" and pedido.reserva_expira_em:
        pagar_ate = pedido.reserva_expira_em.astimezone(BRASILIA)
    na_loja = pedido.canal_venda == "loja"
    montado = {
        "id_pedido": pedido.id_pedido,
        "codigo_venda": pedido.codigo_venda,
        "marca": unidade.marca,
        "feito_em": pedido.criado_em.astimezone(BRASILIA).date(),
        "na_loja": na_loja,
        "modalidade": pedido.modalidade,
        "status": pedido.status,
        "valor_total": pedido.valor_total,
        "itens": [{"produto": i["produto"], "cor": i["cor"], "tamanho": i["tamanho"], "quantidade": i["quantidade"]}
                  for i in itens],
        "retirada": retirada,
        "pagar_ate": pagar_ate,
        "entregue_em": pedido.entregue_em.astimezone(BRASILIA).date() if pedido.entregue_em else None,
        "troca_ate": troca_ate,
        "devolucao": pedido.devolucao,
        "motivo_cancelamento": pedido.motivo_cancelamento,
        "pagamentos": [_montar_pagamento(lancamento) for lancamento in lancamentos],
    }
    montado["texto"] = _texto_do_pedido(montado, unidade, hoje)
    return montado


def _texto_da_troca(pedido: dict, hoje: date) -> str | None:
    if pedido["devolucao"] == "total":
        return "Foi devolvido."
    parcial = "Teve devolução parcial. " if pedido["devolucao"] == "parcial" else ""
    if pedido["troca_ate"] is None:
        return parcial.strip() or None
    if hoje > pedido["troca_ate"]:
        return f"{parcial}O prazo para trocar ou devolver terminou em {_data(pedido['troca_ate'])}."
    return (f"{parcial}Dá para trocar ou devolver até {_data(pedido['troca_ate'])}, numa loja da "
            f"{pedido['marca'].capitalize()}.")


def _texto_dos_estornos(pedido: dict) -> list[str]:
    return [f"Estorno de {_reais(p['valor'])} ({NOME_DO_METODO.get(p['metodo'], p['metodo'])}): "
            f"{STATUS_DO_ESTORNO.get(p['status'], p['status'])} em {_data(p['em'])}."
            for p in pedido["pagamentos"] if p["tipo"] == "estorno"]


def _texto_do_pedido(pedido: dict, unidade: Unidade, hoje: date) -> str:
    inicio = f"Pedido {pedido['codigo_venda']} ({pedido['marca'].capitalize()}), de {_data(pedido['feito_em'])}"
    if pedido["na_loja"] and pedido["status"] != "cancelado":
        situacao = f"compra feita na {unidade.nome}"
    elif pedido["status"] == "cancelado" and pedido["motivo_cancelamento"] in MOTIVO_DO_CANCELAMENTO:
        situacao = MOTIVO_DO_CANCELAMENTO[pedido["motivo_cancelamento"]]
    else:
        situacao = STATUS_DO_PEDIDO.get(pedido["status"], pedido["status"])
    retirada = pedido["retirada"]
    if retirada and retirada["retirar_ate"]:
        situacao += f" na {retirada['loja']} ({retirada['endereco']}) até {_data(retirada['retirar_ate'])}"
    elif retirada:
        situacao += f", para retirar na {retirada['loja']} ({retirada['endereco']})"
    if pedido["pagar_ate"]:
        situacao += f" até {pedido['pagar_ate'].strftime('%d/%m/%Y às %H:%M')}"
    itens = ", ".join(f"{i['quantidade']}x {i['produto']} ({i['cor']}, {i['tamanho']})" for i in pedido["itens"])
    partes = [f"{inicio}: {situacao}. Itens: {itens}. Total {_reais(pedido['valor_total'])}."]
    troca = _texto_da_troca(pedido, hoje)
    if troca:
        partes.append(troca)
    partes.extend(_texto_dos_estornos(pedido))
    return " ".join(partes)


# os últimos pedidos da conta ou um pedido dela; pedido de outra pessoa responde como inexistente.
# Cliente sem conta da migração só tem compras da Vulto antiga: o assistente diz que não há pedidos do site
def meus_pedidos(db: Session, canal: str, id_externo: str, id_pedido: int | None, momento: datetime) -> dict:
    conversa = _conversa_confirmada(db, canal, id_externo, momento)
    if conversa.id_cliente is None:
        resultado = {"items": [], "texto": SEM_PEDIDOS_NO_SITE}
        _afirmar(db, conversa, "meus_pedidos", resultado, momento)
        return resultado
    if id_pedido is not None:
        linha = pedido_repository.pedido_com_nomes(db, id_pedido)
        if linha is None or linha[0].id_cliente != conversa.id_cliente:
            raise RecursoNaoEncontrado("Pedido não encontrado")
        linhas = [linha]
    else:
        linhas, _ = pedido_repository.listar_pedidos(db, PEDIDOS_NA_CONVERSA, 0, id_cliente=conversa.id_cliente)
    pedidos = [pedido for pedido, _, _ in linhas]
    ids = [p.id_pedido for p in pedidos]
    itens = pedido_repository.itens_dos_pedidos(db, ids)
    lancamentos = pedido_repository.pagamentos_dos_pedidos(db, ids)
    lojas = repo.unidades(db, list({p.id_unidade for p in pedidos}))
    hoje = momento.astimezone(BRASILIA).date()
    montados = [_montar_pedido(p, lojas[p.id_unidade], itens[p.id_pedido], lancamentos.get(p.id_pedido, []),
                               _troca_ate(db, p), hoje) for p in pedidos]
    texto = "\n".join(p["texto"] for p in montados) or "Você ainda não tem pedidos."
    resultado = {"items": montados, "texto": texto}
    _afirmar(db, conversa, "meus_pedidos", resultado, momento)
    return resultado


# ---------- chamado: passar para uma pessoa sem sair da conversa ----------

TAMANHO_DA_DESCRICAO = 5000  # o mesmo limite do chamado aberto pelo site
QUEM_FALOU = {"cliente": "Cliente", "assistente": "Assistente"}


def _sem_conta_para_chamado() -> str:
    return (f"Para falar com a nossa equipe é preciso ter conta no site. Crie a sua em {settings.SITE_URL}/cadastro "
            "e depois me chame de novo por aqui.")


# o resumo confirmado pelo cliente vem primeiro; a conversa vai depois, cortada nas falas mais antigas
# se passar do tamanho do chamado
def _descricao(resumo: str, conversa: list[dict]) -> str:
    if not conversa:
        return resumo
    cabecalho = f"{resumo}\n\nConversa com o assistente:\n"
    falas = [f"{QUEM_FALOU[f['de']]}: {f['texto']}" for f in conversa]
    while falas and len(cabecalho) + len("\n".join(falas)) > TAMANHO_DA_DESCRICAO:
        falas.pop(0)
    return cabecalho + "\n".join(falas) if falas else resumo


# chamado é de cliente com conta confirmada nesta conversa, um aberto por vez
def _conferir_quem_abre_chamado(db: Session, conversa: ConversaCanal, momento: datetime) -> None:
    if not _confirmado(conversa, momento):
        raise NaoAutenticado("Confirme quem é você antes: peça o CPF")
    if conversa.id_cliente is None:
        raise RegraDeNegocio(_sem_conta_para_chamado())
    _soltar_chamado_concluido(db, conversa)
    if conversa.id_chamado is not None:
        raise Conflito(f"Você já tem o chamado #{conversa.id_chamado} aberto: a equipe responde por aqui.")


def _texto_do_rascunho(rascunho: dict) -> str:
    return (f"Resumo do que vou enviar para a nossa equipe:\n\n{rascunho['assunto']}\n{rascunho['resumo']}\n\n"
            "Posso enviar?")


# a IA montou o resumo; ele fica guardado e o cliente confere antes de chegar no atendente. As mesmas
# conferências do [Enviar] rodam aqui, para o cliente não confirmar um resumo que não vai virar chamado.
# Chamar de novo troca o rascunho (o cliente pediu para corrigir e a IA refez)
def rascunhar_chamado(db: Session, canal: str, id_externo: str, dados: dict, momento: datetime) -> dict:
    conversa = _conversa(db, canal, id_externo, travar=True)
    _conferir_quem_abre_chamado(db, conversa, momento)
    if dados["id_pedido"] is not None:
        pedido = atendimento_repository.buscar_pedido(db, dados["id_pedido"])
        if pedido is None or pedido.id_cliente != conversa.id_cliente:
            raise RecursoNaoEncontrado("Pedido não encontrado")

    conversa.rascunho_chamado = dados
    conversa.etapa = "confirmando_chamado"
    db.commit()
    return {"mensagem": _texto_do_rascunho(dados)}


# depois do botão [Enviar]: abre com o rascunho guardado, não com o que vier na chamada. Usa a mesma regra do
# chamado aberto pelo site (pedido só do próprio cliente, prioridade sugerida em segundo plano). Daqui em
# diante, toda mensagem do cliente também vai para o chamado, até a equipe concluir
def abrir_chamado(db: Session, canal: str, id_externo: str, falas: list[dict], momento: datetime) -> dict:
    conversa = _conversa(db, canal, id_externo, travar=True)
    _conferir_quem_abre_chamado(db, conversa, momento)
    rascunho = conversa.rascunho_chamado
    if conversa.etapa != "confirmando_chamado" or rascunho is None:
        raise RegraDeNegocio("Não encontrei o resumo para enviar. Pode me contar de novo o que aconteceu?")

    cliente = usuario_repository.buscar_por_id(db, conversa.id_cliente)
    chamado = atendimento.abrir_chamado(db, cliente, {
        "categoria": rascunho["categoria"],
        "assunto": rascunho["assunto"],
        "descricao": _descricao(rascunho["resumo"], falas),
        "id_pedido": rascunho["id_pedido"],
        "canal_externo": canal,
        "aberto_pelo_assistente": True,
    })
    conversa.rascunho_chamado = None
    conversa.id_chamado = chamado["id_chamado"]
    conversa.etapa = "em_chamado"
    resumo_conversa.marcar_virou_chamado(db, conversa)
    db.commit()
    aviso = _aviso_da_equipe(equipe_volta_em(momento), momento) or "A equipe responde por aqui mesmo."
    return {"id_chamado": chamado["id_chamado"], "mensagem": f"Pronto, abri o chamado #{chamado['id_chamado']}. {aviso}"}


# enquanto o chamado está aberto, o n8n grava aqui toda mensagem do cliente (e também a passa para a IA).
# Não pede a confirmação de novo: o chamado ficou ligado a esta conversa quando o cliente se confirmou
def mensagem_no_chamado(db: Session, canal: str, id_externo: str, texto: str) -> dict:
    conversa = _conversa(db, canal, id_externo, travar=True)
    _soltar_chamado_concluido(db, conversa)
    if conversa.id_chamado is None:
        db.commit()
        raise RegraDeNegocio("Esta conversa não tem chamado aberto")
    chamado = atendimento_repository.buscar_chamado(db, conversa.id_chamado)
    cliente = usuario_repository.buscar_por_id(db, chamado.id_cliente)
    return atendimento.enviar_mensagem_do_cliente(db, cliente, chamado.id_chamado, texto)


# ---------- texto da conversa (90 dias, para revisar onde o assistente falhou) ----------

def _mascarar(texto: str) -> str:
    return CODIGO_NO_TEXTO.sub("[código]", CPF_NO_TEXTO.sub("[CPF]", texto))


# o n8n manda, depois de cada resposta, a mensagem do cliente e a resposta do assistente (que pode não ter
# saído, como no limite do dia). Fica ligado à conta só se ela está confirmada na conversa
def guardar_texto(db: Session, canal: str, id_externo: str, cliente: str, assistente: str | None,
                  momento: datetime) -> None:
    conversa = _conversa(db, canal, id_externo)
    confirmado = _confirmado(conversa, momento)
    falas = [("cliente", cliente)] + ([("assistente", assistente)] if assistente else [])
    for papel, texto in falas:
        db.add(MensagemConversa(
            canal=canal, id_externo=id_externo,
            id_cliente=conversa.id_cliente if confirmado else None,
            id_cliente_sem_conta=conversa.id_cliente_sem_conta if confirmado else None,
            papel=papel, texto=_mascarar(texto), criado_em=momento,
        ))
    db.commit()
