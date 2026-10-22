from sqlalchemy import Select, select
from sqlalchemy.orm import Session, aliased

from src.models.atendimento import Chamado
from src.models.catalogo import ImagemProduto, Produto, Variante
from src.models.estoque import Transferencia, Unidade
from src.models.vendas import Pedido
from src.repositories.atendimento_repository import da_marca

# A marca de cada registro, para conferir se a pessoa da equipe a vê (ver_marca_lorenzi, ver_marca_vulto).
# Registro que não existe volta vazio: o use case responde 404 depois

Destino = aliased(Unidade, name="destino")


def _marcas(db: Session, consulta: Select) -> set[str]:
    return set(db.scalars(consulta))


def da_unidade(db: Session, id_unidade: int) -> set[str]:
    return _marcas(db, select(Unidade.marca).where(Unidade.id_unidade == id_unidade))


# a venda é da marca da unidade (a mesma regra da Visão Geral)
def do_pedido(db: Session, id_pedido: int) -> set[str]:
    return _marcas(db, select(Unidade.marca).join(Pedido, Pedido.id_unidade == Unidade.id_unidade)
                   .where(Pedido.id_pedido == id_pedido))


# a transferência fica dentro da marca; origem e destino entram para não esconder um dado antigo diferente
def da_transferencia(db: Session, id_transferencia: int) -> set[str]:
    linha = db.execute(
        select(Unidade.marca, Destino.marca)
        .join(Transferencia, Transferencia.id_unidade_origem == Unidade.id_unidade)
        .join(Destino, Destino.id_unidade == Transferencia.id_unidade_destino)
        .where(Transferencia.id_transferencia == id_transferencia)
    ).first()
    return set(linha) if linha else set()


# o chamado é de cada marca ligada a ele (unidade, pedido, peça ou migração); a dúvida geral não tem marca
def do_chamado(db: Session, id_chamado: int) -> set[str]:
    return {marca for marca in ("lorenzi", "vulto")
            if db.scalar(select(Chamado.id_chamado).where(Chamado.id_chamado == id_chamado, da_marca(marca)))}


def do_produto(db: Session, id_produto: int) -> set[str]:
    return _marcas(db, select(Produto.marca).where(Produto.id_produto == id_produto))


def da_variante(db: Session, id_variante: int) -> set[str]:
    return _marcas(db, select(Produto.marca).join(Variante, Variante.id_produto == Produto.id_produto)
                   .where(Variante.id_variante == id_variante))


def da_imagem(db: Session, id_imagem: int) -> set[str]:
    return _marcas(db, select(Produto.marca).join(ImagemProduto, ImagemProduto.id_produto == Produto.id_produto)
                   .where(ImagemProduto.id_imagem == id_imagem))

