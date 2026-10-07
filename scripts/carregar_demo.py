# Carrega o cenário de demonstração: unidades, catálogo, modelos de acesso, contas,
# estoque com 30 dias de movimentações (para o histórico ter o que mostrar) e pedidos
# em cada etapa, com uma avaliação e uma denúncia para a moderação.
# Pode rodar de novo: o que já existe é pulado, nada é duplicado.
# Uso, na raiz do repositório com o venv ativo:
#   python -m scripts.carregar_demo
# Precisa de SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY e DATABASE_URL no .env.
# A senha das contas de demonstração é pedida na hora (ou lida de DEMO_SENHA) e não fica no código.
import os
import random
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from getpass import getpass
from zoneinfo import ZoneInfo

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from src.models.catalogo import CategoriaProduto, HistoricoPreco, Produto, Variante
from src.models.contas import ModeloAcesso, ModeloPermissao, Permissao, Usuario
from src.models.estoque import Estoque, MovimentacaoEstoque, Unidade
from src.models.vendas import EnderecoCliente, Pedido
from src.repositories import usuario_repository
from src.use_cases import avaliacoes, pedidos, vendas
from src.use_cases.erros import ErroNegocio
from src.utils.cpf import digito_verificador
from src.utils.supabase_admin import ErroSupabase, SupabaseAdmin

BRASILIA = ZoneInfo("America/Sao_Paulo")
DIAS_DE_HISTORICO = 30

# ---------- dados ----------

UNIDADES = [
    dict(nome="CD Guarulhos", tipo="cd", despacha_online=True, rua="Rodovia Hélio Smidt", numero="1500",
         bairro="Cumbica", cidade="Guarulhos", uf="SP", cep="07190100"),
    dict(nome="Loja Paulista", tipo="loja", despacha_online=True, rua="Avenida Paulista", numero="1000",
         bairro="Bela Vista", cidade="São Paulo", uf="SP", cep="01310100"),
    dict(nome="Loja Pinheiros", tipo="loja", despacha_online=False, rua="Rua dos Pinheiros", numero="500",
         bairro="Pinheiros", cidade="São Paulo", uf="SP", cep="05422001"),
]

# (categoria, nome, prefixo do SKU, preço, cores, tamanhos, descrição técnica, descrição para o cliente)
PRODUTOS = [
    ("Camisetas", "Camiseta Básica de Algodão", "CAM-BAS", "79.90", ["Branca", "Preta"], ["P", "M", "G"],
     "Malha 100% algodão, fio 30.1 penteado, gramatura 160 g/m².",
     "A camiseta que combina com tudo, macia e confortável para o dia a dia."),
    ("Camisetas", "Camiseta Listrada", "CAM-LIS", "89.90", ["Azul"], ["P", "M", "G"],
     "Malha 100% algodão fio tinto, listras de 1 cm.",
     "Listras clássicas para um visual leve e despojado."),
    ("Calças", "Calça Jeans Reta", "CAL-JEA", "199.90", ["Azul Escuro"], ["38", "40", "42"],
     "Denim 98% algodão e 2% elastano, 12 oz, lavagem escura.",
     "Modelagem reta que valoriza a silhueta, com um toque de elasticidade."),
    ("Calças", "Calça de Alfaiataria", "CAL-ALF", "249.90", ["Preta", "Bege"], ["38", "40"],
     "Tecido de viscose com poliéster, cós com passantes e bolsos faca.",
     "Elegante e versátil, do escritório ao jantar."),
    ("Vestidos", "Vestido Midi Floral", "VES-MID", "259.90", ["Floral"], ["P", "M", "G"],
     "Viscose 100% estampada, forro em microfibra, comprimento midi.",
     "Estampa floral delicada e caimento fluido para os dias quentes."),
    ("Acessórios", "Bolsa de Couro", "ACE-BOL", "349.90", ["Caramelo"], ["U"],
     "Couro bovino legítimo, forro de algodão, alça regulável de 120 cm.",
     "Bolsa espaçosa em couro legítimo, para carregar o dia inteiro com estilo."),
]

# permissões de cada modelo, como na tabela da seção 6 do case (o Admin já vem da migration)
MODELOS = {
    "Funcionário": [
        "movimentar_estoque", "definir_estoque_minimo", "solicitar_transferencia", "enviar_transferencia",
        "receber_transferencia", "registrar_venda_fisica", "preparar_entregar_pedido",
        "cancelar_pedido_equipe", "corrigir_cadastro_cliente", "atender_chamado",
    ],
    "Estoquista": [
        "movimentar_estoque", "definir_estoque_minimo", "solicitar_transferencia", "enviar_transferencia",
        "receber_transferencia",
    ],
    "Atendente": ["atender_chamado", "moderar_avaliacoes"],
}

# e-mails terminados em .example, domínio reservado para exemplos: nunca chegam a uma pessoa real.
# Conta interna usa o e-mail corporativo; conta de cliente, o e-mail pessoal.
DOMINIO_CORPORATIVO = "casalorenzi.example"
DOMINIO_PESSOAL = "email.example"
EMAIL_ESTOQUISTA = f"rafael.souza@{DOMINIO_CORPORATIVO}"

# o modelo Admin já existe (migration); os outros são criados acima a partir de MODELOS
CONTAS_INTERNAS = [
    dict(nome="Carolina Mendes", email=f"carolina.mendes@{DOMINIO_CORPORATIVO}", modelo="Admin", unidade=None),
    dict(nome="Fernanda Lima", email=f"fernanda.lima@{DOMINIO_CORPORATIVO}", modelo="Funcionário",
         unidade="Loja Paulista"),
    dict(nome="Rafael Souza", email=EMAIL_ESTOQUISTA, modelo="Estoquista", unidade="CD Guarulhos"),
    dict(nome="Juliana Alves", email=f"juliana.alves@{DOMINIO_CORPORATIVO}", modelo="Atendente", unidade=None),
]


def _cpf_de(base: str) -> str:
    cpf = base + digito_verificador(base)
    return cpf + digito_verificador(cpf)


CLIENTES = [
    dict(nome="Marina Costa", email=f"marina.costa@{DOMINIO_PESSOAL}", cpf=_cpf_de("390533447")),
    dict(nome="Pedro Henrique", email=f"pedro.henrique@{DOMINIO_PESSOAL}", cpf=_cpf_de("714602938")),
    # funcionária comprando: conta de cliente pessoal, separada da conta interna (case, seção 5)
    dict(nome="Fernanda Lima", email=f"fernanda.lima@{DOMINIO_PESSOAL}", cpf=_cpf_de("258147369")),
]

# CPF de quem comprou na loja e ainda não tem conta: ao se cadastrar pelo site com ele,
# a compra aparece em Meus pedidos (ADR 0014)
CPF_NA_NOTA = _cpf_de("605944493")

# estoque mínimo por canal nas lojas: algumas variantes terminam abaixo e geram alerta
MINIMO_LOJA_FISICA = 4
MINIMO_ONLINE_CD = 15


def variantes_do_catalogo() -> list[dict]:
    variantes = []
    for categoria, nome, prefixo, preco, cores, tamanhos, *_ in PRODUTOS:
        for cor in cores:
            for tamanho in tamanhos:
                variantes.append(dict(
                    produto=nome, cor=cor, tamanho=tamanho, preco=Decimal(preco),
                    sku=f"{prefixo}-{cor[:3].upper()}-{tamanho}",
                ))
    return variantes


# ---------- movimentações dos últimos 30 dias ----------

def montar_movimentacoes(agora: datetime) -> list[dict]:
    """Movimentações em ordem de data, sem deixar saldo negativo em nenhum momento.
    Sempre gera a mesma lista (semente fixa), então rodar de novo dá o mesmo cenário."""
    sorteio = random.Random(13)
    inicio = (agora - timedelta(days=DIAS_DE_HISTORICO)).astimezone(BRASILIA).replace(
        hour=9, minute=0, second=0, microsecond=0
    )
    saldos: dict[tuple, int] = {}
    movimentacoes = []

    def mover(dia, hora, sku, unidade, canal, tipo, quantidade, motivo=None):
        chave = (sku, unidade, canal)
        if saldos.get(chave, 0) + quantidade < 0:
            return
        saldos[chave] = saldos.get(chave, 0) + quantidade
        quando = inicio + timedelta(days=dia, hours=hora)
        movimentacoes.append(dict(
            sku=sku, unidade=unidade, canal=canal, tipo=tipo, quantidade=quantidade, motivo=motivo,
            criado_em=quando.astimezone(timezone.utc),
        ))

    skus = [v["sku"] for v in variantes_do_catalogo()]

    # dia 0: carga inicial (ADR 0006: saldo inicial entra como movimentação)
    for sku in skus:
        mover(0, 0, sku, "CD Guarulhos", "online", "saldo_inicial", sorteio.randint(20, 40))
        for loja in ("Loja Paulista", "Loja Pinheiros"):
            mover(0, 1, sku, loja, "loja_fisica", "saldo_inicial", sorteio.randint(3, 8))
            mover(0, 1, sku, loja, "online", "saldo_inicial", sorteio.randint(1, 4))

    # dia 9: chega mercadoria nova no CD
    for sku in sorteio.sample(skus, k=len(skus) // 2):
        mover(9, 2, sku, "CD Guarulhos", "online", "recebimento", sorteio.randint(10, 20))

    # dia 16: contagem de inventário na Paulista corrige algumas peças
    for sku in sorteio.sample(skus, k=4):
        mover(16, 3, sku, "Loja Paulista", "loja_fisica", "ajuste", -1, "Diferença na contagem de inventário")

    # dia 20: peças danificadas no provador em Pinheiros
    for sku in sorteio.sample(skus, k=3):
        mover(20, 5, sku, "Loja Pinheiros", "loja_fisica", "avaria", -1, "Peça manchada no provador")

    # dia 23: a Paulista passa peças do online para a vitrine (saída e entrada juntas)
    for sku in sorteio.sample(skus, k=3):
        if saldos.get((sku, "Loja Paulista", "online"), 0) >= 1:
            mover(23, 4, sku, "Loja Paulista", "online", "saida_realocacao", -1)
            mover(23, 4, sku, "Loja Paulista", "loja_fisica", "entrada_realocacao", 1)

    # dia 27: peças que sumiram na contagem
    for sku in sorteio.sample(skus, k=2):
        mover(27, 6, sku, "Loja Pinheiros", "loja_fisica", "perda", -2, "Peças não localizadas na contagem")

    # dias 25 a 29: o CD despacha para a rede e o saldo do online cai
    for dia in range(25, 30):
        for sku in sorteio.sample(skus, k=5):
            mover(dia, 3, sku, "CD Guarulhos", "online", "ajuste", -sorteio.randint(3, 8),
                  "Ajuste de demonstração: saída para a rede")

    # o trigger aplica o saldo na ordem de inserção e o histórico soma por data: as duas
    # ordens precisam ser a mesma. Cada variante, unidade e canal já foi gerado em ordem,
    # então ordenar (de forma estável) não cria saldo negativo.
    movimentacoes.sort(key=lambda m: m["criado_em"])
    return movimentacoes


# ---------- carga no banco ----------

def _buscar_ou_criar(db: Session, modelo, filtro: dict, **campos):
    existente = db.scalar(select(modelo).filter_by(**filtro))
    if existente is not None:
        return existente, False
    novo = modelo(**filtro, **campos)
    db.add(novo)
    db.flush()
    return novo, True


def carregar_base(db: Session) -> dict:
    """Unidades, catálogo (com histórico de preço) e modelos de acesso, numa transação."""
    criados = dict(unidades=0, variantes=0, modelos=0)
    for dados in UNIDADES:
        _, novo = _buscar_ou_criar(db, Unidade, {"nome": dados["nome"]},
                                   **{k: v for k, v in dados.items() if k != "nome"})
        criados["unidades"] += novo

    for categoria, nome, _, _, _, _, desc_tecnica, desc_cliente in PRODUTOS:
        cat, _ = _buscar_ou_criar(db, CategoriaProduto, {"nome": categoria})
        _buscar_ou_criar(db, Produto, {"nome": nome}, id_categoria=cat.id_categoria,
                         descricao_tecnica=desc_tecnica, descricao_cliente=desc_cliente)

    for dados in variantes_do_catalogo():
        produto = db.scalar(select(Produto).where(Produto.nome == dados["produto"]))
        variante, nova = _buscar_ou_criar(
            db, Variante, {"sku": dados["sku"]},
            id_produto=produto.id_produto, cor=dados["cor"], tamanho=dados["tamanho"], preco=dados["preco"],
        )
        if nova:
            # toda definição de preço vai para o histórico, inclusive a da criação
            db.add(HistoricoPreco(id_variante=variante.id_variante, preco_novo=dados["preco"]))
            criados["variantes"] += 1

    for nome, codigos in MODELOS.items():
        modelo, novo = _buscar_ou_criar(db, ModeloAcesso, {"nome": nome})
        if novo:
            ids = db.scalars(select(Permissao.id_permissao).where(Permissao.codigo.in_(codigos))).all()
            if len(ids) != len(codigos):
                raise RuntimeError(f"Permissões do modelo {nome} não existem: rode as migrations")
            db.add_all(ModeloPermissao(id_modelo=modelo.id_modelo, id_permissao=i) for i in ids)
            criados["modelos"] += 1

    db.commit()
    return criados


def _criar_conta(db: Session, auth: SupabaseAdmin, senha: str, dados: dict, **campos) -> bool:
    """Login no Supabase Auth + linha em USUARIO (ADR 0008). Fora de transação de banco:
    o login é criado antes e apagado se a linha não puder ser gravada."""
    if usuario_repository.email_em_uso(db, dados["email"]):
        return False

    id_usuario = usuario_repository.buscar_login(db, dados["email"])
    login_novo = id_usuario is None
    if login_novo:
        id_usuario = auth.criar_login(dados["email"], senha)

    try:
        db.add(Usuario(id_usuario=id_usuario, nome=dados["nome"], email=dados["email"],
                       status_conta="ativa", **campos))
        db.commit()
    except Exception:
        db.rollback()
        if login_novo:
            auth.apagar_login(id_usuario)
        raise
    return True


def carregar_contas(db: Session, auth: SupabaseAdmin, senha: str) -> int:
    criadas = 0
    for dados in CONTAS_INTERNAS:
        modelo = db.scalar(select(ModeloAcesso).where(ModeloAcesso.nome == dados["modelo"]))
        if modelo is None:
            raise RuntimeError(f"Modelo {dados['modelo']} não existe: rode as migrations (alembic upgrade head)")
        unidade = db.scalar(select(Unidade).where(Unidade.nome == dados["unidade"])) if dados["unidade"] else None
        criadas += _criar_conta(
            db, auth, senha, dados, tipo_conta="interna", id_modelo_acesso=modelo.id_modelo,
            id_unidade=unidade.id_unidade if unidade else None,
        )
    for dados in CLIENTES:
        criadas += _criar_conta(db, auth, senha, dados, tipo_conta="cliente", cpf=dados["cpf"])
    return criadas


def carregar_estoque(db: Session, agora: datetime) -> int:
    """Movimentações (o trigger monta o saldo, ADR 0005) e estoque mínimo. Só roda uma vez:
    se as variantes da demonstração já têm movimentação, não faz nada."""
    skus = [v["sku"] for v in variantes_do_catalogo()]
    id_por_sku = dict(db.execute(select(Variante.sku, Variante.id_variante).where(Variante.sku.in_(skus))).all())
    ja_carregado = db.scalar(
        select(MovimentacaoEstoque.id_movimentacao)
        .where(MovimentacaoEstoque.id_variante.in_(id_por_sku.values())).limit(1)
    )
    if ja_carregado is not None:
        return 0

    id_por_unidade = dict(db.execute(select(Unidade.nome, Unidade.id_unidade)).all())
    estoquista = db.scalar(select(Usuario.id_usuario).where(Usuario.email == EMAIL_ESTOQUISTA))

    movimentacoes = montar_movimentacoes(agora)
    # inseridas uma a uma, na ordem das datas, para o trigger aplicar o saldo na mesma ordem
    for m in movimentacoes:
        db.add(MovimentacaoEstoque(
            id_variante=id_por_sku[m["sku"]], id_unidade=id_por_unidade[m["unidade"]], canal=m["canal"],
            id_usuario=estoquista, tipo=m["tipo"], quantidade=m["quantidade"], motivo=m["motivo"],
            criado_em=m["criado_em"],
        ))
        db.flush()

    # o mínimo não é saldo: pode ser alterado direto (o trigger só protege a quantidade)
    minimos = [
        ("loja_fisica", [id_por_unidade["Loja Paulista"], id_por_unidade["Loja Pinheiros"]], MINIMO_LOJA_FISICA),
        ("online", [id_por_unidade["CD Guarulhos"]], MINIMO_ONLINE_CD),
    ]
    for canal, unidades, minimo in minimos:
        db.execute(
            update(Estoque)
            .where(Estoque.canal == canal, Estoque.id_unidade.in_(unidades),
                   Estoque.id_variante.in_(id_por_sku.values()))
            .values(estoque_minimo=minimo, minimo_alterado_por=estoquista, minimo_alterado_em=agora)
        )
    db.commit()
    return len(movimentacoes)


# ---------- vendas, atendimento ao pedido e avaliações ----------

def _conta(db: Session, email: str) -> Usuario:
    return db.scalar(select(Usuario).where(Usuario.email == email))


# variantes com pelo menos `minimo` peças disponíveis naquela unidade e canal, na ordem do SKU
def _skus_disponiveis(db: Session, unidade: Unidade, canal: str, minimo: int = 1) -> list[Variante]:
    return list(db.scalars(
        select(Variante).join(Estoque, Estoque.id_variante == Variante.id_variante)
        .where(Estoque.id_unidade == unidade.id_unidade, Estoque.canal == canal,
               Estoque.quantidade - Estoque.quantidade_reservada >= minimo)
        .order_by(Variante.sku)
    ))


def _pagar(db: Session, cliente: Usuario, pedido: dict) -> dict:
    cobrado = pedidos.criar_cobranca(db, cliente, pedido["id_pedido"], "pix")
    return pedidos.responder_cobranca(db, cliente, cobrado["pagamentos"][-1].id_pagamento, True)


def carregar_vendas(db: Session) -> dict:
    """Pedidos em cada etapa, pelas mesmas regras da API (use cases). Só roda uma vez: se já existe
    algum pedido, não faz nada. Devolve os códigos de venda que a demonstração usa."""
    if db.scalar(select(Pedido.id_pedido).limit(1)) is not None:
        return {}

    vendedora = _conta(db, f"fernanda.lima@{DOMINIO_CORPORATIVO}")
    marina = _conta(db, CLIENTES[0]["email"])
    pedro = _conta(db, CLIENTES[1]["email"])
    fernanda = _conta(db, CLIENTES[2]["email"])
    paulista = db.scalar(select(Unidade).where(Unidade.nome == "Loja Paulista"))
    cd = db.scalar(select(Unidade).where(Unidade.nome == "CD Guarulhos"))

    # venda física com CPF (vai para a conta da Marina) e sem CPF (pode ser reivindicada pelo código)
    na_loja = _skus_disponiveis(db, paulista, "loja_fisica", 2)
    codigos = {}
    for cpf, variante in ((marina.cpf, na_loja[0]), (None, na_loja[1])):
        pedido = vendas.registrar_venda_fisica(db, vendedora, {
            "id_unidade": paulista.id_unidade, "cpf_nota": cpf,
            "itens": [{"id_variante": variante.id_variante, "quantidade": 1}],
            "pagamentos": [{"metodo": "cartao_debito" if cpf else "dinheiro", "valor": variante.preco}],
        })
        codigos["venda na loja sem CPF" if cpf is None else "venda na loja da Marina"] = pedido["codigo_venda"]

    enderecos_salvos = {}
    for cliente in (marina, pedro):
        endereco = EnderecoCliente(id_cliente=cliente.id_usuario, rua="Rua Augusta", numero="1500",
                                   complemento="Apto 12", bairro="Consolação", cidade="São Paulo", uf="SP",
                                   cep="01304001")
        db.add(endereco)
        db.commit()
        enderecos_salvos[cliente.id_usuario] = endereco.id_endereco

    no_cd = _skus_disponiveis(db, cd, "online", 3)

    # Pedro: entrega em casa paga, esperando a equipe preparar (bloco da Visão Geral)
    pedido = pedidos.checkout(db, pedro, {
        "itens": [{"id_variante": no_cd[0].id_variante, "quantidade": 1}], "modalidade": "entrega",
        "id_endereco": enderecos_salvos[pedro.id_usuario], "id_unidade_retirada": None,
    })
    codigos["entrega paga para preparar"] = _pagar(db, pedro, pedido)["codigo_venda"]

    # Marina: retirada na Paulista, paga e pronta para retirar
    online_paulista = _skus_disponiveis(db, paulista, "online")
    pedido = pedidos.checkout(db, marina, {
        "itens": [{"id_variante": online_paulista[0].id_variante, "quantidade": 1}], "modalidade": "retirada",
        "id_endereco": None, "id_unidade_retirada": paulista.id_unidade,
    })
    _pagar(db, marina, pedido)
    codigos["retirada pronta"] = vendas.marcar_pronto_para_retirada(db, pedido["id_pedido"])["codigo_venda"]

    # Marina: entrega em casa já entregue e avaliada; o Pedro acha útil e a Fernanda denuncia
    pedido = pedidos.checkout(db, marina, {
        "itens": [{"id_variante": no_cd[1].id_variante, "quantidade": 2}], "modalidade": "entrega",
        "id_endereco": enderecos_salvos[marina.id_usuario], "id_unidade_retirada": None,
    })
    _pagar(db, marina, pedido)
    vendas.enviar(db, pedido["id_pedido"])
    entregue = vendas.entregar(db, pedido["id_pedido"], None)
    codigos["entrega entregue e avaliada"] = entregue["codigo_venda"]
    avaliacao = avaliacoes.criar(db, None, marina, {
        "id_item_pedido": entregue["itens"][0]["id_item"], "nota": 5,
        "texto": "Tecido ótimo e chegou antes do prazo. Comprei dois!",
    })
    avaliacoes.votar_util(db, None, pedro, avaliacao["id_avaliacao"])
    avaliacoes.denunciar(db, fernanda, avaliacao["id_avaliacao"], "Avaliação de demonstração para a moderação")
    return codigos


def carregar_venda_com_cpf_na_nota(db: Session) -> str | None:
    """Venda na loja com o CPF na nota de alguém sem conta. Só roda uma vez: se já existe pedido com
    esse CPF na nota (ou uma conta com ele), não faz nada. Devolve o código da venda."""
    ja_existe = db.scalar(select(Pedido.id_pedido).where(Pedido.cpf_nota == CPF_NA_NOTA).limit(1))
    if ja_existe is not None or usuario_repository.cpf_em_uso(db, CPF_NA_NOTA):
        return None
    vendedora = _conta(db, f"fernanda.lima@{DOMINIO_CORPORATIVO}")
    paulista = db.scalar(select(Unidade).where(Unidade.nome == "Loja Paulista"))
    variante = _skus_disponiveis(db, paulista, "loja_fisica")[0]
    pedido = vendas.registrar_venda_fisica(db, vendedora, {
        "id_unidade": paulista.id_unidade, "cpf_nota": CPF_NA_NOTA,
        "itens": [{"id_variante": variante.id_variante, "quantidade": 1}],
        "pagamentos": [{"metodo": "pix", "valor": variante.preco}],
    })
    return pedido["codigo_venda"]


def main() -> None:
    # importado aqui para os dados e as funções acima poderem ser usados (e testados) sem conectar no banco
    from src.database.session import SessionLocal

    senha = os.getenv("DEMO_SENHA")
    if not senha:
        senha = getpass("Senha das contas de demonstração (mínimo 6 caracteres, não aparece na tela): ")
        if getpass("Repita a senha: ") != senha:
            raise SystemExit("As senhas não conferem.")

    agora = datetime.now(timezone.utc)
    with SessionLocal() as db:
        try:
            base = carregar_base(db)
            contas = carregar_contas(db, SupabaseAdmin(), senha)
            movimentacoes = carregar_estoque(db, agora)
            codigos = carregar_vendas(db)
            codigo_cpf_na_nota = carregar_venda_com_cpf_na_nota(db)
        except (ErroSupabase, RuntimeError, ErroNegocio) as erro:
            raise SystemExit(str(erro))

    print(f"Unidades novas: {base['unidades']}, variantes novas: {base['variantes']}, "
          f"modelos novos: {base['modelos']}")
    print(f"Contas novas: {contas} (contas que já existiam mantêm a senha antiga)")
    print(f"Movimentações de estoque: {movimentacoes or 'já carregadas antes, nada novo'}")
    if codigos:
        print("Pedidos de demonstração (código da venda):")
        for descricao, codigo in codigos.items():
            print(f"  {codigo}  {descricao}")
    else:
        print("Pedidos: já existiam pedidos no banco, nada novo")
    cpf = f"{CPF_NA_NOTA[:3]}.{CPF_NA_NOTA[3:6]}.{CPF_NA_NOTA[6:9]}-{CPF_NA_NOTA[9:]}"
    if codigo_cpf_na_nota:
        print(f"  {codigo_cpf_na_nota}  venda na loja com CPF na nota {cpf}, sem conta: cadastre-se pelo site com ele")
    else:
        print(f"Venda com CPF na nota ({cpf}): já existia, nada novo")


if __name__ == "__main__":
    main()
