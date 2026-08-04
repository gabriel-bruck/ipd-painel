import hashlib
from django.db import models
from client.models import ProjetoIPD, ProjetoCliente

class IPD(models.Model):
    profile = models.CharField(max_length=150)
    
    # Métricas da medição
    fama = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    engaj = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    valencia = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    mob = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    interesse = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    ipd = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    
    data = models.DateField()
    data_registro = models.DateTimeField(auto_now_add=True)
    
    # Chaves estrangeiras
    projeto_ipd = models.ForeignKey(
        ProjetoIPD, 
        on_delete=models.CASCADE, 
        related_name='medicoes_ipd'
    )
    projeto_cliente = models.ForeignKey(
        ProjetoCliente, 
        on_delete=models.CASCADE, 
        related_name='medicoes_ipd'
    )
    
    # Hash automático para substituição/update na importação da planilha
    hash_indice = models.CharField(max_length=64, unique=True, editable=False)

    class Meta:
        db_table = 'ipd_medicoes'
        verbose_name = 'Medição IPD'
        verbose_name_plural = 'Medições IPD'
        constraints = [
            models.UniqueConstraint(
                fields=['projeto_ipd', 'profile', 'data'], 
                name='unique_ipd_profile_data'
            )
        ]

    def save(self, *args, **kwargs):
        raw_string = f"{self.projeto_ipd_id}-{self.profile}-{self.data.strftime('%Y-%m-%d')}"
        self.hash_indice = hashlib.sha256(raw_string.encode('utf-8')).hexdigest()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.profile} - {self.data} ({self.ipd})"


class Conteudo(models.Model):
    # ID customizado passado via post (API/script)
    id_post = models.CharField(max_length=255, primary_key=True)
    
    titulo = models.CharField(max_length=200)
    texto = models.TextField()
    data_registro = models.DateTimeField(auto_now_add=True, db_index=True)
    data = models.DateField(db_index=True)
    
    # Relacionamentos
    projeto_cliente = models.ForeignKey(
        ProjetoCliente, 
        on_delete=models.CASCADE, 
        related_name='conteudos'
    )
    projeto_ipd = models.ForeignKey(
        ProjetoIPD, 
        on_delete=models.CASCADE, 
        related_name='conteudos'
    )
    ipds = models.ManyToManyField(IPD, related_name='conteudos', blank=True)

    class Meta:
        db_table = 'ipd_conteudos'
        verbose_name = 'Conteúdo'
        verbose_name_plural = 'Conteúdos'

    def __str__(self):
        return f"Post {self.id_post}: {self.titulo}"