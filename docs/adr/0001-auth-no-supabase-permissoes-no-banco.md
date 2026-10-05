# Login pelo Supabase Auth, tipo de conta e permissões no banco

**Contexto:** o plano inicial era guardar senha e tokens em tabelas próprias. Com a migração para o Supabase, o Auth passou a oferecer login, links e sessão prontos, e também metadados por usuário.

**Decisão:** o Supabase Auth cuida só de e-mail, senha, links e sessão. A tabela de usuários usa o mesmo id do Auth e guarda tipo de conta, CPF, modelo de acesso e status. Tipo de conta e permissões nunca ficam nos metadados do Auth, e as permissões são lidas do banco a cada ação.

**Por quê:** não duplicamos o que o Auth já resolve, e os metadados do Auth podem ser editados pelo próprio usuário, então não servem para guardar o que ele pode fazer. Ler do banco a cada ação faz mudanças de permissão valerem na hora.
