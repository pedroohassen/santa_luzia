# Cobertura efetiva universal da Atenção Primária à Saúde em Santa Luzia/MG

Trabalho da disciplina **Pesquisa Operacional para Saúde** (UFMG), Prof. João Flávio de Freitas Almeida.

Autores: Arthur Soares Guimarães, Messias Inácio da Silva Carvalho e Pedro Hassen Coura Vieira.

Repositório: https://github.com/pedroohassen/santa_luzia

## O que é

Modelo de programação linear inteira mista que decide quantas equipes de Saúde da Família (eSF) criar, se elas
vão para as UBS existentes (até o porte de cada uma) ou para UBS novas, e a qual unidade vai cada setor
censitário, inteiro. O objetivo é que toda a população tenha uma unidade de APS a até 3 km (urbano) ou 5 km
(rural) pela rede de ruas e vaga numa equipe (até 3.500 pessoas por eSF, NT nº 2/2025-SAPS/MS).

- **Modelo base:** lexicográfico (1º menos eSF novas; 2º menos obra; 3º menor distância total).
- **Modelo integrado (comparação):** custo anual em R$ do governo (eSF e obras) e da população (deslocamento),
  na linha de Almeida et al. (2019).

## Conteúdo

| Pasta | Conteúdo |
|---|---|
| `1_modelo/` | `modelo_aps.mod` e `acessibilidade.mod` (GMPL/GLPK), scripts em Python e a pasta `dat/`, com os `.dat` do GLPK |
| `2_dados/` | Dados do modelo em `.xlsx` (base de saúde, porte das UBS e matriz de distâncias pela rede de ruas) e os arquivos da rede de ruas |
| `3_resultados/` | Solução de cada cenário (plano, eSF em UBS existentes, alocação dos setores, resumo) e tabelas |
| `4_artigo_latex/` | Artigo em LaTeX (`artigo.tex` e `figuras/`) e o PDF compilado |
| `5_relatorio_e_apresentacao/` | Relatório completo e apresentação, em PDF |

### Arquivos .dat

| Arquivo (`1_modelo/dat/`) | Para que serve |
|---|---|
| `C1_`, `C2_`, `C3_etapa1_min_equipes.dat` | Modelo base, etapa 1: menor número de eSF novas |
| `..._etapa2_min_obras.dat` | Etapa 2: menor obra, com o número de eSF da etapa 1 |
| `..._etapa3_min_distancia.dat` | Etapa 3: menor distância total, com eSF e obra das etapas anteriores |
| `..._integrado_custo_anual.dat` | Modelo integrado (custo anual em R$) |
| `acessibilidade_C1_beta1.5.dat` | Índice gravitacional (`acessibilidade.mod`), C1 e β = 1,5 |

Cenários: **C1** = raios de 3/5 km e 100% de cobertura; **C2** = raios de 5/10 km e 100%; **C3** = raios de 3/5 km
e 99%, com o restante atendido por uma eSF volante.

## Como conferir (poucos segundos)

Requer Python 3.10 ou mais recente.

```
pip install -r requirements.txt
cd 1_modelo
python resolver.py conferir
```

O comando lê as soluções gravadas em `3_resultados/` e confere cada uma, restrição por restrição, fora do solver
(cobertura, raio, setor inteiro, porte e capacidade).

## Como resolver de novo

**Python** (o GLPK lê o modelo `.mod` e o HiGHS resolve), dentro de `1_modelo/`:

```
python gera_dados.py               # testa a leitura dos dados e escreve dat/dados_base.dat
python resolver.py cenarios        # linha de base, C1, C2 e C3
python resolver.py curva           # curva de cobertura do C1
python resolver.py sensibilidade   # 10 variantes do C1
python resolver.py integrado       # modelo integrado em R$/ano
python resolver.py repolir         # melhora a designação do C1 a C3 a partir do integrado (mesmas unidades)
python resolver.py dat             # refaz os .dat do GLPK
python robustez.py                 # Monte Carlo e crescimento da população
python volante.py                  # eSF volante (C3)
python acessibilidade.py           # índices de acessibilidade espacial
python encaminhamentos.py          # níveis secundário e terciário
python analise_antes_depois.py     # comparação antes e depois
```

Cada cenário pode levar de minutos a algumas horas, porque as variáveis são binárias. Estes comandos gravam por
cima de `3_resultados/`. Para um cenário só, com limite de tempo (em segundos) por etapa e para a linha de base:
`python resolver.py C1 --tempo=600 --tempo-atual=60`. Para partir da solução gravada, acrescente `--inicio=C1`.

**GLPK** (linha de comando), dentro de `1_modelo/dat/`:

```
glpsol --math ../modelo_aps.mod --data C1_etapa1_min_equipes.dat --tmlim 3600
glpsol --math ../acessibilidade.mod --data acessibilidade_C1_beta1.5.dat
```

O GLPK lê e monta os modelos em segundos, mas pode levar horas para resolvê-los (cerca de 39 mil variáveis
binárias no C1 e 76 mil no C2). Os resultados do trabalho foram obtidos com o HiGHS, a partir do mesmo `.mod`.

**Distâncias:** `python distancias_rede.py` refaz a matriz a partir da rede de ruas gravada em
`2_dados/rede_osm_pe.graphml` (não é preciso rodar: a matriz já está em `2_dados/matriz_distancias_rede.xlsx`).

**Artigo:** em `4_artigo_latex/`, rode `pdflatex artigo.tex` duas vezes (ou envie a pasta ao Overleaf).

## Resultados principais

| | Hoje | C1 | C2 | C3 |
|---|---|---|---|---|
| Cobertura efetiva | 85,2% | 100% | 100% | 99% + eSF volante |
| eSF novas (em UBS existentes + em UBS novas) | – | 16 (9 + 7) | 10 (10 + 0) | 10 (10 + 0) |
| Obras | – | R$ 13,92 mi | – | – |
| Esforço anual (% da despesa com APS) | – | 26,1% | 12,4% | 14,3% |

C1: etapas 1 e 2 com ótimo provado. C2 e C3: a etapa 1 parou no limite de tempo (limite inferior de 9 eSF).

## Dados e licenças

IBGE (Censo 2022, malha municipal, CEMPRE), Ministério da Saúde (CNES, e-Gestor APS, NT nº 2/2025-SAPS/MS, Novo
PAC Saúde), RAG 2025 de Santa Luzia e rede de ruas do OpenStreetMap (© colaboradores do OpenStreetMap, ODbL).

## Declaração de uso de inteligência artificial

O trabalho foi elaborado com o apoio de modelos de linguagem (LLMs); os autores revisaram o conteúdo e assumem a
responsabilidade pelas premissas, análises e conclusões.
