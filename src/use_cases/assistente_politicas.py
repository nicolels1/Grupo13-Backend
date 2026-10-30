from decimal import Decimal
from typing import get_args

from sqlalchemy.orm import Session

from src.entities.comum import Marca
from src.entities.vendas import Metodo, MetodoOnline
from src.models.fidelidade import RegraFidelidade
from src.repositories import fidelidade_repository
from src.use_cases import devolucoes, fidelidade, pedidos
from src.use_cases.assistente_conta import DIAS_PARA_RETIRAR, NOME_DO_METODO, _numero, _pontos, _reais

# as regras que o cliente pergunta sem login (frete, troca, pagamento, pontos), para o assistente não responder
# prazo nem valor de cabeça. Todo número sai da constante que a regra usa ou da regra vigente de pontos: se a
# regra mudar, a resposta muda junto


def _decimal(valor: Decimal) -> str:
    return f"{valor.normalize():f}".replace(".", ",")


# o valor do ponto tem até 4 casas (R$ 0,0125): com centavos inteiros fica com 2, sem arredondar o resto
def _valor_do_ponto(valor: Decimal) -> str:
    valor = valor.normalize()
    if valor.as_tuple().exponent > -2:
        valor = valor.quantize(Decimal("0.01"))
    return "R$ " + f"{valor:f}".replace(".", ",")


def _texto_dos_pontos(regra: RegraFidelidade) -> str:
    nome = regra.marca.capitalize()
    ganho = f"cada R$ 1 em compras dá {_decimal(regra.pontos_por_real)} " \
            f"{'ponto' if regra.pontos_por_real == 1 else 'pontos'}"
    if regra.multiplicador_online > 1:
        ganho += f", multiplicado por {_decimal(regra.multiplicador_online)} nas compras pelo site ou pelo app"
    partes = [f"{nome}: {ganho}.", f"Cada ponto ganho hoje vale {_valor_do_ponto(regra.valor_ponto)} no resgate."]
    if regra.bonus_ouro > 0:
        partes.append(f"Na categoria Ouro, os pontos ganhos têm mais {_decimal(regra.bonus_ouro * 100)}%.")
    if regra.validade_meses is None:
        partes.append("Os pontos ganhos hoje ainda não têm prazo de vencimento.")
    elif regra.validade_meses_ouro != regra.validade_meses:
        partes.append(f"Os pontos vencem {regra.validade_meses} meses depois de ganhos "
                      f"({regra.validade_meses_ouro} meses na categoria Ouro).")
    else:
        partes.append(f"Os pontos vencem {regra.validade_meses} meses depois de ganhos.")
    partes.append(f"A categoria sobe pelos pontos ganhos no ano: Prata a partir de {_numero(regra.limite_prata)} "
                  f"e Ouro a partir de {_numero(regra.limite_ouro)}.")
    partes.append(f"Dá para usar a partir de {_pontos(regra.resgate_minimo)}, no pagamento do site ou no caixa da "
                  "loja, com desconto menor que o valor das peças.")
    partes.append(f"Os pontos da {nome} só valem na {nome}.")
    return " ".join(partes)


def _regra_de_pontos(regra: RegraFidelidade) -> dict:
    campos = ("marca", "valor_ponto", "pontos_por_real", "multiplicador_online", "bonus_ouro", "validade_meses",
              "validade_meses_ouro", "limite_prata", "limite_ouro", "resgate_minimo")
    return {campo: getattr(regra, campo) for campo in campos} | {"texto": _texto_dos_pontos(regra)}


def _lista(nomes: list[str]) -> str:
    return ", ".join(nomes[:-1]) + f" ou {nomes[-1]}" if len(nomes) > 1 else nomes[0]


def _texto_da_entrega() -> str:
    minutos = int(pedidos.RESERVA.total_seconds() // 60)
    return (f"Entrega em casa: frete de {_reais(pedidos.FRETE_ENTREGA)}, grátis quando as peças somam a partir de "
            f"{_reais(pedidos.FRETE_GRATIS_A_PARTIR_DE)} (conta o carrinho inteiro, mesmo com as duas marcas). "
            "Retirar na loja não tem frete. Ao fechar a compra, as peças ficam reservadas por "
            f"{minutos} minutos para o pagamento. O pedido para retirar fica na loja por {DIAS_PARA_RETIRAR} dias "
            "depois de ficar pronto; depois disso, é cancelado e o valor é estornado.")


def _texto_da_troca() -> str:
    return (f"Troca e devolução: até {devolucoes.PRAZO.days} dias depois da entrega, numa loja da mesma marca da "
            "peça (o centro de distribuição não atende o público), levando a peça e um documento. A troca é por "
            "outra cor ou tamanho do mesmo produto. Na devolução, o estorno volta pelo mesmo meio de pagamento.")


def _texto_do_pagamento() -> str:
    no_site = [NOME_DO_METODO[m] for m in get_args(MetodoOnline)]
    so_na_loja = [NOME_DO_METODO[m] for m in get_args(Metodo) if m not in get_args(MetodoOnline)]
    texto = f"No site, o pagamento é por {_lista(no_site)}."
    if so_na_loja:
        texto += f" Na loja, também dá para pagar em {_lista(so_na_loja)}."
    return texto + (" O pedido pode ser cancelado pelo site enquanto aguarda pagamento; depois de pago, "
                    "o cancelamento é com a nossa equipe.")


TEXTO_DA_CONTA = (
    "Para entrar no site, use o e-mail ou o CPF com a senha. Compra feita na loja sem CPF na nota entra na sua "
    "conta pelo código da venda do comprovante, uma vez só. E-mail ou CPF errados no cadastro são corrigidos em "
    "qualquer loja, com um documento."
)


def politicas(db: Session) -> dict:
    regras = [fidelidade_repository.regra_vigente(db, marca, fidelidade.hoje()) for marca in get_args(Marca)]
    return {
        "pontos": [_regra_de_pontos(regra) for regra in regras if regra is not None],
        "entrega": _texto_da_entrega(),
        "troca_devolucao": _texto_da_troca(),
        "pagamento": _texto_do_pagamento(),
        "conta": TEXTO_DA_CONTA,
    }
