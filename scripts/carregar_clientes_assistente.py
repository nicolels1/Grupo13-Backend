# Cria os clientes de teste do assistente do cliente (Entrega 4), para a banca testar pontos e pedidos.
# O código de confirmação vai por e-mail, então os clientes usam uma caixa do Gmail criada só para isso
# (nunca o e-mail da empresa): o Gmail entrega <caixa>+ana@gmail.com e <caixa>+bruno@gmail.com na mesma caixa.
# Cada cliente ganha pedidos de retirada já pagos, que creditam pontos nas marcas (trigger do pagamento).
# Pode rodar de novo: conta que já existe é pulada, com os pedidos dela.
# Uso, na raiz do repositório com o venv ativo:
#   python -m scripts.carregar_clientes_assistente
# Precisa de SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, DATABASE_URL e ASSISTENTE_CAIXA_TESTE no .env.
# A senha do login no site é pedida na hora (ou lida de DEMO_SENHA) e não fica no código.
import os
from getpass import getpass

from sqlalchemy import select
from sqlalchemy.orm import Session

from scripts.carregar_demo import _conta, _cpf_de, _criar_conta, _pagar
from src.models.catalogo import CategoriaProduto, Produto, Variante
from src.models.estoque import Estoque, Unidade
from src.use_cases import pedidos, vendas
from src.use_cases.erros import ErroNegocio
from src.utils.supabase_admin import ErroSupabase, SupabaseAdmin

# (nome, apelido no e-mail, CPF, pedidos: (marca, pronto para retirar?))
CLIENTES_TESTE = [
    ("Ana Teste", "ana", _cpf_de("481730256"), [("lorenzi", True), ("vulto", False)]),
    ("Bruno Teste", "bruno", _cpf_de("926315847"), [("vulto", True)]),
]


# caixa@gmail.com + ana -> caixa+ana@gmail.com: o Gmail entrega na mesma caixa
def email_com_apelido(caixa: str, apelido: str) -> str:
    usuario, dominio = caixa.strip().split("@")
    return f"{usuario}+{apelido}@{dominio}"


def cpf_formatado(cpf: str) -> str:
    return f"{cpf[:3]}.{cpf[3:6]}.{cpf[6:9]}-{cpf[9:]}"


# uma peça à venda com estoque online numa loja da marca, para o pedido de retirada
def _peca_para_retirar(db: Session, marca: str) -> tuple[Unidade, Variante] | None:
    disponivel = Estoque.quantidade - Estoque.quantidade_reservada
    return db.execute(
        select(Unidade, Variante)
        .join(Estoque, Estoque.id_unidade == Unidade.id_unidade)
        .join(Variante, Variante.id_variante == Estoque.id_variante)
        .join(Produto, Produto.id_produto == Variante.id_produto)
        .join(CategoriaProduto, CategoriaProduto.id_categoria == Produto.id_categoria)
        .where(Unidade.marca == marca, Unidade.tipo == "loja", Unidade.ativo, Estoque.canal == "online",
               disponivel >= 1, Variante.ativo, Produto.ativo, CategoriaProduto.ativo)
        .order_by(Unidade.id_unidade, Variante.sku)
        .limit(1)
    ).first()


def _comprar_para_retirar(db: Session, cliente, marca: str, pronto: bool) -> str | None:
    achado = _peca_para_retirar(db, marca)
    if achado is None:
        return None
    unidade, variante = achado
    pedido = pedidos.checkout(db, cliente, {
        "itens": [{"id_variante": variante.id_variante, "quantidade": 1}], "modalidade": "retirada",
        "id_endereco": None, "id_unidade_retirada": unidade.id_unidade,
    })
    _pagar(db, cliente, pedido)
    if pronto:
        vendas.marcar_pronto_para_retirada(db, pedido["id_pedido"])
    situacao = "pronto para retirar" if pronto else "pago, em preparação"
    return f"{pedido['codigo_venda']}  {marca}, {situacao} na {unidade.nome}"


def carregar(db: Session, auth: SupabaseAdmin, senha: str, caixa: str) -> list[dict]:
    resultado = []
    for nome, apelido, cpf, compras in CLIENTES_TESTE:
        dados = {"nome": nome, "email": email_com_apelido(caixa, apelido)}
        nova = _criar_conta(db, auth, senha, dados, tipo_conta="cliente", cpf=cpf)
        feitos = []
        if nova:
            cliente = _conta(db, dados["email"])
            for marca, pronto in compras:
                feito = _comprar_para_retirar(db, cliente, marca, pronto)
                feitos.append(feito or f"sem peça com estoque online numa loja {marca}: pedido não criado")
        resultado.append({**dados, "cpf": cpf, "nova": nova, "pedidos": feitos})
    return resultado


def main() -> None:
    # importado aqui para as funções acima poderem ser testadas sem conectar no banco
    from src.database.session import SessionLocal

    caixa = os.getenv("ASSISTENTE_CAIXA_TESTE")
    if not caixa or "@" not in caixa:
        raise SystemExit("Defina ASSISTENTE_CAIXA_TESTE no .env com o Gmail dos clientes de teste.")
    senha = os.getenv("DEMO_SENHA")
    if not senha:
        senha = getpass("Senha do login no site dos clientes de teste (mínimo 6 caracteres): ")
        if getpass("Repita a senha: ") != senha:
            raise SystemExit("As senhas não conferem.")

    with SessionLocal() as db:
        try:
            clientes = carregar(db, SupabaseAdmin(), senha, caixa)
        except (ErroSupabase, RuntimeError, ErroNegocio) as erro:
            raise SystemExit(str(erro))

    for cliente in clientes:
        print(f"{cliente['nome']}  CPF {cpf_formatado(cliente['cpf'])}  {cliente['email']}")
        if not cliente["nova"]:
            print("  já existia: nada novo")
        for pedido in cliente["pedidos"]:
            print(f"  {pedido}")


if __name__ == "__main__":
    main()
