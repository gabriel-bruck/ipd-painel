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
    interesse = models.DecimalField(
    max_digits=10,
    decimal_places=2,
    null=True,
    blank=True,
    default=None,
)
    ipd = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    
    data = models.DateField()
    data_registro = models.DateTimeField(auto_now_add=True)
    
    # Chaves estrangeiras
    projeto_ipd = models.ForeignKey(
        ProjetoIPD, 
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
    # Nova chave primária personalizada: {id_post}_{projeto_ipd_id}
    id = models.CharField(max_length=500, primary_key=True, editable=False)

    # ID original do post recebido via API/script
    id_post = models.CharField(max_length=255, db_index=True)

    # Relacionamento 1-para-Muitos (Cada registro pertence a um IPD específico)
    projeto_ipd = models.ForeignKey(
        'client.ProjetoIPD',
        on_delete=models.CASCADE,
        related_name='conteudos'
    )

    profile = models.CharField(max_length=150, null=True, blank=True)
    texto = models.TextField()
    data_registro = models.DateTimeField(auto_now_add=True, db_index=True)
    data = models.DateField(db_index=True)
    curtidas = models.IntegerField(default=0)
    comentarios = models.IntegerField(default=0)
    link_post = models.CharField(max_length=1000, blank=True, null=True, db_index=True)

    # Categoria agora é individual para cada combinação de Post + IPD
    categoria_tema = models.CharField(max_length=255, default='Outros', db_index=True)

    class Meta:
        db_table = 'ipd_conteudos'
        verbose_name = 'Conteúdo'
        verbose_name_plural = 'Conteúdos'
        ordering = ['-data', '-curtidas']

        # Garante a integridade: não permite duplicar a combinação id_post + IPD
        constraints = [
            models.UniqueConstraint(
                fields=['id_post', 'projeto_ipd'],
                name='unique_post_per_ipd'
            )
        ]

        indexes = [
            # Índices otimizados para consultas filtradas por IPD
            models.Index(
                fields=['projeto_ipd', 'profile', 'data'],
                name='idx_cnt_ipd_prof_data'
            ),
            models.Index(
                fields=['projeto_ipd', 'data', '-curtidas'],
                name='idx_cnt_ipd_data_curt_desc'
            ),

            # Índices legados
            models.Index(
                fields=['profile', 'data', '-curtidas'], 
                name='idx_cnt_prof_data_curt_desc'
            ),
            models.Index(
                fields=['data', '-curtidas'], 
                name='idx_cnt_data_curt_desc'
            ),
            models.Index(
                fields=['profile', 'data'], 
                name='idx_cnt_profile_data'
            ),
            models.Index(
                fields=['data', '-comentarios'], 
                name='idx_cnt_data_coment_desc'
            ),
            models.Index(
                fields=['-data_registro'], 
                name='idx_cnt_data_registro_desc'
            ),
        ]

    def save(self, *args, **kwargs):
        # Sobrescreve o save para gerar a chave composta antes de persisitir
        if not self.id and self.id_post and self.projeto_ipd_id:
            self.id = f"{self.id_post}_{self.projeto_ipd_id}"
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Post {self.id_post} [IPD: {self.projeto_ipd_id}]: {self.texto[:30]}"

import hashlib

from django.db import models
from client.models import ProjetoCliente


class ResumoExecutivo(models.Model):
    projeto = models.ForeignKey(
        ProjetoCliente,
        on_delete=models.CASCADE,
        related_name="resumos_executivos",
    )

    mes_referencia = models.CharField(
        max_length=7,
        db_index=True,
    )

    # Hash dos dados utilizados para gerar o resumo.
    # Se os dados mudarem, o hash também muda.
    hash_insumo = models.CharField(
        max_length=64,
        db_index=True,
    )

    conteudo = models.TextField()

    criado_em = models.DateTimeField(
        auto_now_add=True
    )

    atualizado_em = models.DateTimeField(
        auto_now=True
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "projeto",
                    "mes_referencia",
                ],
                name="unique_resumo_executivo_projeto_mes",
            )
        ]

        indexes = [
            models.Index(
                fields=[
                    "projeto",
                    "mes_referencia",
                    "hash_insumo",
                ]
            )
        ]

        verbose_name = "Resumo Executivo"
        verbose_name_plural = "Resumos Executivos"

    def __str__(self):
        return (
            f"{self.projeto} - "
            f"{self.mes_referencia}"
        )