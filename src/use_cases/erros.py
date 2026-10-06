# erros esperados das regras de negócio; o tratador em middlewares/erros.py
# transforma cada um na resposta {"detail": mensagem} com o status da classe
class ErroNegocio(Exception):
    status_code = 400

    def __init__(self, mensagem: str):
        super().__init__(mensagem)
        self.mensagem = mensagem


class NaoAutenticado(ErroNegocio):
    status_code = 401


class RecursoNaoEncontrado(ErroNegocio):
    status_code = 404


class SemPermissao(ErroNegocio):
    status_code = 403


# conflito com o estado atual, ex.: e-mail já usado, chamado já assumido
class Conflito(ErroNegocio):
    status_code = 409


# dado válido no formato, mas recusado pela regra, ex.: estoque insuficiente
class RegraDeNegocio(ErroNegocio):
    status_code = 422


class MuitasTentativas(ErroNegocio):
    status_code = 429


# serviço externo (ex.: Supabase Auth) fora do ar ou sem resposta
class ServicoIndisponivel(ErroNegocio):
    status_code = 503
