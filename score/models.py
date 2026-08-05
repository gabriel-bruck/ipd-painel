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
    profile = models.CharField(max_length=150, null=True, blank=True)
    texto = models.TextField()
    data_registro = models.DateTimeField(auto_now_add=True, db_index=True)
    data = models.DateField(db_index=True)
    curtidas = models.IntegerField(default=0)
    comentarios = models.IntegerField(default=0)
    link_post = models.CharField(max_length=1000, blank=True, null=True, db_index=True)
    
    # Relacionamentos
    projeto_ipd = models.ManyToManyField(ProjetoIPD, blank=True)

    class Meta:
        db_table = 'ipd_conteudos'
        verbose_name = 'Conteúdo'
        verbose_name_plural = 'Conteúdos'
        ordering = ['-data', '-curtidas']  # Ordenação padrão no banco

        indexes = [
            # 1. Busca por Perfil em um Período/Data ordenado pelas Curtidas (Mais populares do Perfil)
            models.Index(
                fields=['profile', 'data', '-curtidas'], 
                name='idx_cnt_prof_data_curt_desc'
            ),
            
            # 2. Busca por Período/Data ordenado pelas Curtidas (Top Posts Gerais por Data)
            models.Index(
                fields=['data', '-curtidas'], 
                name='idx_cnt_data_curt_desc'
            ),

            # 3. Busca por Perfil e Data (Filtros rápidos de Perfil no Tempo)
            models.Index(
                fields=['profile', 'data'], 
                name='idx_cnt_profile_data'
            ),

            # 4. Análise de Engajamento/Repercussão (Busca rápida pelos posts mais comentados)
            models.Index(
                fields=['data', '-comentarios'], 
                name='idx_cnt_data_coment_desc'
            ),

            # 5. Auditoria e Cronologia (Consulta rápida por data de inserção no sistema)
            models.Index(
                fields=['-data_registro'], 
                name='idx_cnt_data_registro_desc'
            ),
        ]

    def __str__(self):
        return f"Post {self.id_post}: {self.texto[:30]})"