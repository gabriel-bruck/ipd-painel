from django.db import models
from django.contrib.auth.models import User

class ProjetoIPD(models.Model):
    nome = models.CharField(max_length=200)

    class Meta:
        db_table = 'projeto_ipd'
        verbose_name = 'Projeto IPD'
        verbose_name_plural = 'Projetos IPD'

    def __str__(self):
        return self.nome


class ProjetoCliente(models.Model):
    TIPO_IPD_CHOICES = [
        (1, "Tipo 1"),
        (2, "Tipo 2"),
    ]

    nome = models.CharField(max_length=200)
    slug = models.SlugField(max_length=250, unique=True, blank=True)
    descricao = models.TextField(blank=True, null=True)
    tipo_ipd = models.PositiveSmallIntegerField(
        choices=TIPO_IPD_CHOICES,
        default=1,
    )
    cliente = models.CharField(max_length=200)
    projetos_ipd = models.ManyToManyField(
    ProjetoIPD, 
    through='ProjetoClienteIPD',
    related_name='projetos_cliente', 
    blank=True
)
    

    class Meta:
        db_table = 'projeto_cliente'
        verbose_name = 'Projeto Cliente'
        verbose_name_plural = 'Projetos Clientes'

    def __str__(self):
        return f"{self.cliente} - {self.nome}"


class ProjetoClienteIPD(models.Model):
    projeto_cliente = models.ForeignKey(ProjetoCliente, on_delete=models.CASCADE)
    projeto_ipd = models.ForeignKey(ProjetoIPD, on_delete=models.CASCADE)
    profiles_usados = models.JSONField(
        default=list, 
        help_text="Lista de perfis monitorados para este IPD neste cliente específico"
    )

    class Meta:
        db_table = 'projeto_cliente_ipd'
        unique_together = ('projeto_cliente', 'projeto_ipd')

    def __str__(self):
        return f"{self.projeto_cliente.nome} -> {self.projeto_ipd.nome}"