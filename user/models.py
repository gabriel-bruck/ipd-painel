from django.db import models
from client.models import ProjetoCliente
from django.contrib.auth.models import User
# Create your models here.
class PermissoesUsuario(models.Model):
    user = models.OneToOneField(
        User, 
        on_delete=models.CASCADE, 
        related_name='permissoes'
    )
    projetos_cliente = models.ManyToManyField(
        ProjetoCliente, 
        related_name='usuarios_autorizados', 
        blank=True
    )

    class Meta:
        db_table = 'permissoes_usuario'
        verbose_name = 'Permissão de Usuário'
        verbose_name_plural = 'Permissões de Usuários'

    def __str__(self):
        return f"Permissões: {self.user.username}"