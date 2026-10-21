# importa os models para registrá-los no Base.metadata (o Alembic lê daqui)
from src.models.contas import (  # noqa: F401
    ClienteSemConta,
    ModeloAcesso,
    ModeloPermissao,
    Permissao,
    Usuario,
    UsuarioPermissaoExcecao,
    auth_users,
)
from src.models.catalogo import (  # noqa: F401
    CategoriaProduto,
    Destaque,
    HistoricoPreco,
    ImagemProduto,
    Produto,
    Variante,
)
from src.models.estoque import (  # noqa: F401
    Estoque,
    ItemTransferencia,
    MovimentacaoEstoque,
    Transferencia,
    Unidade,
)
from src.models.vendas import (  # noqa: F401
    EnderecoCliente,
    EnderecoEntrega,
    ItemPedido,
    Pagamento,
    Pedido,
)
from src.models.atendimento import Chamado, HistoricoChamado, Mensagem  # noqa: F401
from src.models.avaliacoes import (  # noqa: F401
    Avaliacao,
    DenunciaAvaliacao,
    FotoAvaliacao,
    VotoUtil,
)
from src.models.migracao import OrigemMigracao  # noqa: F401
from src.models.fidelidade import (  # noqa: F401
    ConsumoPontos,
    LotePontos,
    MovimentoPontos,
    RegraFidelidade,
    SaldoPontos,
)
