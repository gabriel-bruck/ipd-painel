from django.db import models
from django.contrib.auth.models import User 

class Projeto(models.Model):
    nome = models.CharField(max_length=100)
    descricao = models.TextField(blank=True, null=True)
    data_criacao = models.DateTimeField(auto_now_add=True)
    
    # Controle de permissão seletiva por Usuário:
    # Um usuário pode estar em vários projetos, e um projeto tem vários usuários.
    membros = models.ManyToManyField(
        User, 
        related_name='projetos_permitidos',
        blank=True,
        help_text="Usuários que têm permissão para visualizar/acessar este projeto."
    )

    def __str__(self):
        return self.nome