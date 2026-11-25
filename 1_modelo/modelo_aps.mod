/* ================================================================
   modelo_aps.mod
   Plano de Cobertura Efetiva da Atencao Primaria - Santa Luzia/MG

   Sistema modelado: Atencao Primaria a Saude (APS) do SUS no municipio,
   organizada pela Estrategia Saude da Familia (eSF), com territorios
   adscritos as Unidades Basicas de Saude (UBS).

   Cobertura efetiva: o morador tem uma unidade de APS dentro da distancia
   maxima (pelo trajeto na rede de ruas) E vaga nas equipes dessa unidade
   (no maximo CAP pessoas por eSF, NT n. 2/2025-SAPS/MS).
     META = 1 : 100% da populacao;
     META < 1 : pelo menos META x populacao (ex.: 0,99 no cenario C3).

   Decisoes:
     (1) x: a qual unidade vai a populacao de cada setor censitario
            (o setor vai INTEIRO para uma unica unidade; uma unidade recebe
            varios setores);
     (2) a: quantas eSF novas cada UBS existente recebe, ate o seu porte
            (numero maximo de eSF que o predio comporta);
     (3) w: onde abrir UBS novas, e de que porte.

   Funcoes objetivo:
     Modelo base (lexicografico, resolvido em etapas, sem pesos):
       ETAPA 1: minimizar as novas equipes (em equivalentes de eSF)
       ETAPA 2: minimizar o investimento em obras, mantendo o otimo da etapa 1
       ETAPA 3: minimizar a distancia total (pessoa x km), mantendo 1 e 2
       ETAPA 0: curva de cobertura - maximizar a populacao coberta com no
                maximo LIM_EQ novas equipes (usar com OBRIG = 0)
     Modelo de comparacao (integrado, uma unica etapa):
       ETAPA 4: minimizar o custo anual total em R$:
                custeio das eSF novas (governo)
              + obras anualizadas pela vida util (governo)
              + custo de deslocamento da populacao (pessoas)
   ================================================================ */

/* ---------------------------- CONJUNTOS ---------------------------- */
set S;                     /* setores censitarios (pontos de demanda)        */
set U;                     /* UBS existentes                                 */
set A;                     /* pontos de apoio rurais existentes (sem equipe) */
set N;                     /* locais candidatos a UBS nova                   */
set T;                     /* tipos de UBS nova (porte / tipo de equipe)     */
set J := U union A union N;/* todos os pontos de atendimento                 */

/* --------------------------- PARAMETROS ---------------------------- */
param pop{S} >= 0;                 /* populacao do setor                          */
param nurb{S} binary;              /* 1 se o setor fica fora da area urbanizada   */
param dist{S, J} >= 0, default 999;/* distancia setor -> ponto (km, rede de ruas) */
param DMAX_URB > 0;                /* dist. max., setor em area urbanizada (IBGE) */
param DMAX_NURB > 0;               /* dist. max., demais setores                  */
param CAP > 0;                     /* pessoas por eSF (NT 2/2025-SAPS/MS: 3.500)  */
param eq{U} >= 0, integer;         /* equipes eSF atuais em cada UBS (CNES)       */
param amp{U} >= 0, integer, default 0; /* eSF que ainda cabem na UBS (porte - eq) */
param mae{A} symbolic in U;        /* UBS responsavel por cada ponto de apoio     */
param t_eq{T} > 0;                 /* equipes (equivalentes eSF) do tipo novo     */
param t_custo{T} >= 0;             /* custo de obra do tipo novo (R$, Novo PAC)   */
param CUSTO_AMP default 0;         /* custo de obra por eSF acrescida em UBS      */
                                   /* existente (0: o porte ja comporta a equipe) */

param ETAPA integer, in {0, 1, 2, 3, 4};
param OBRIG binary, default 1;     /* 1 = meta de cobertura obrigatoria           */
param META default 1, >= 0, <= 1;  /* fracao minima da populacao coberta          */
param LIM_EQ  default 1e9;         /* limite de novas equipes (vem da etapa 1)    */
param LIM_INV default 1e12;        /* limite de investimento (vem da etapa 2)     */
param EQ_MIN  default 0;           /* corte valido: minimo de novas eSF pela vaga */

/* parametros do modelo integrado (ETAPA 4), em R$ de 2025 */
param C_ESF  default 0;            /* custo anual de uma eSF (R$/ano)             */
param VIDA   default 25;           /* vida util da obra (anos)                    */
param C_DESL default 0;            /* custo social de deslocamento                */
                                   /* (R$ por pessoa x km de distancia, por ano)  */

/* pares setor-ponto permitidos: dentro da distancia maxima; pontos de apoio
   rurais so atendem setores fora da area urbanizada */
set LINK within {S, J} :=
    {i in S, j in J : dist[i,j] <= (if nurb[i] = 1 then DMAX_NURB else DMAX_URB)
                      and (nurb[i] = 1 or not (j in A))};

/* ---------------------------- VARIAVEIS ---------------------------- */
var x{(i,j) in LINK} binary;       /* 1 se o setor i e atendido (inteiro) em j    */
var a{u in U} integer, >= 0, <= amp[u]; /* eSF novas na UBS existente u           */
var w{k in N, t in T} binary;      /* 1 se abre UBS nova em k do tipo t           */

var NOVAS_EQ >= 0;                 /* novas equipes (equivalentes de eSF)         */
var INVEST   >= 0;                 /* investimento total em obras (R$)            */
var DIST_TOT >= 0;                 /* soma de pessoa x km                         */
var COBERTOS >= 0;                 /* populacao com cobertura efetiva             */

/* ------------------------ FUNCAO OBJETIVO -------------------------- */
minimize Objetivo:
      (if ETAPA = 1 then NOVAS_EQ
       else if ETAPA = 2 then INVEST
       else if ETAPA = 3 then DIST_TOT
       else if ETAPA = 4 then C_ESF * NOVAS_EQ + INVEST / VIDA + C_DESL * DIST_TOT
       else -COBERTOS);

/* --------------------------- RESTRICOES ---------------------------- */
/* Definicoes dos indicadores */
s.t. Def_NovasEq: NOVAS_EQ = sum{u in U} a[u] + sum{k in N, t in T} t_eq[t] * w[k,t];

s.t. Def_Invest: INVEST = CUSTO_AMP * sum{u in U} a[u] + sum{k in N, t in T} t_custo[t] * w[k,t];

s.t. Def_Dist: DIST_TOT = sum{(i,j) in LINK} pop[i] * dist[i,j] * x[i,j];

s.t. Def_Cob:  COBERTOS = sum{(i,j) in LINK} pop[i] * x[i,j];

/* R1 - cada setor vai, no maximo, para uma unica unidade */
s.t. R1_Atend{i in S}: sum{(i,j) in LINK} x[i,j] <= 1;

/* R2 - meta de cobertura efetiva (a vaga e garantida por R3 e R4):
        META = 1: todo setor atendido dentro da distancia maxima;
        META < 1: populacao coberta >= META x populacao total */
s.t. R2_Cobertura{i in S : OBRIG = 1 and META >= 1}: sum{(i,j) in LINK} x[i,j] = 1;
s.t. R2_Meta{z in 1..1 : OBRIG = 1 and META < 1}: COBERTOS >= META * sum{i in S} pop[i];

/* R3 - capacidade das UBS existentes (equipes atuais + eSF novas ate o porte):
        populacao propria + a dos seus pontos de apoio */
s.t. R3_CapUBS{u in U}:
      sum{(i,u) in LINK} pop[i] * x[i,u]
    + sum{p in A, (i,p) in LINK : mae[p] = u} pop[i] * x[i,p]
    <= CAP * (eq[u] + a[u]);

/* R4 - capacidade das UBS novas */
s.t. R4_CapNova{k in N}:
      sum{(i,k) in LINK} pop[i] * x[i,k] <= CAP * sum{t in T} t_eq[t] * w[k,t];

/* R5 - no maximo um tipo de UBS por local novo */
s.t. R5_UmTipo{k in N}: sum{t in T} w[k,t] <= 1;

/* R6 - so se atende em UBS nova se ela for aberta (reforco da formulacao) */
s.t. R6_Abre{(i,k) in LINK : k in N}: x[i,k] <= sum{t in T} w[k,t];

/* R7 - limites herdados das etapas anteriores */
s.t. R7_LimEq:  NOVAS_EQ <= LIM_EQ;
s.t. R7_LimInv: INVEST   <= LIM_INV;

/* R8 - corte valido (nao muda o otimo; so ajuda o solver):
        EQ_MIN = arredonda para cima (META x populacao - vagas atuais) / CAP */
s.t. R8_CorteEq{z in 1..1 : OBRIG = 1}:  NOVAS_EQ >= EQ_MIN;

solve;

/* --------------------- RELATORIO (somente GLPK) -------------------- */
printf "\n=================================================\n";
printf "PLANO DE COBERTURA EFETIVA DA APS - Santa Luzia (ETAPA %d)\n", ETAPA;
printf "=================================================\n";
printf "Populacao total:          %12.0f\n", sum{i in S} pop[i];
printf "Cobertura efetiva:        %12.0f (%.2f%%)\n", COBERTOS, 100 * COBERTOS / sum{i in S} pop[i];
printf "Novas equipes (eq. eSF):  %12.2f\n", NOVAS_EQ;
printf "Investimento em obras: R$ %12.2f\n", INVEST;
printf "Distancia media:          %12.3f km\n", DIST_TOT / max(1, COBERTOS);
printf "-------------------------------------------------\n";
printf {u in U : a[u] > 0.5} "eSF nova em UBS existente: %s (+%d)\n", u, a[u];
printf {k in N, t in T : w[k,t] > 0.5} "UBS nova: local %s, tipo %s\n", k, t;
printf "=================================================\n";

end;
