"""Permissões locais para arquivos pessoais controladas somente por código.

Mantenha esta chave como ``False`` enquanto a VEGA estiver em modo de
criação somente. Não há comando de voz, ferramenta ou variável de ambiente
que possa alterá-la durante a execução.
"""

# Para permitir que a VEGA sobrescreva ou exclua arquivos pessoais, altere
# explicitamente esta constante para True e reinicie a aplicação.
ALLOW_PERSONAL_FILE_CHANGES = False
