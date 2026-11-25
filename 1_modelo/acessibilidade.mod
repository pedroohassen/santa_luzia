/* ================================================================
   acessibilidade.mod
   Acessibilidade espacial gravitacional (Hansen, 1959) com ajuste de
   demanda (Joseph e Bantock, 1982), no formato da aula 8 (slides 29 e
   30, Operations Research for Health Care, Lesson 8/15).
   Nao ha variaveis de decisao: o GLPK so calcula os parametros e
   imprime o relatorio. Dados: dados/acessibilidade_<cenario>_beta<b>.dat
   (gerados por acessibilidade.py).
   ================================================================ */
set I;                    /* origens: setores censitarios               */
set J;                    /* destinos: UBS (existentes e novas)         */
set K;                    /* demanda: setores censitarios (K = I)       */

param Beta;               /* decaimento com a distancia (premissa)      */
param Di{I,J}, >= 0;      /* distancia origem-destino (km, >= raio eq.) */
param Dibeta{i in I, j in J} := Di[i,j]^(Beta);
param S{J};               /* oferta: vagas = eSF x 3.500                */
param A{i in I} := sum{j in J} S[j] / Dibeta[i,j];         /* Hansen    */
param P{K};               /* populacao do setor                         */
param Dk{K,J};            /* mesma matriz, vista do lado da demanda     */
param Dkbeta{k in K, j in J} := Dk[k,j]^(Beta);
param V{j in J} := sum{k in K} P[k] / Dkbeta[k,j];         /* demanda   */
param Ai{i in I} := sum{j in J} S[j] / (Dibeta[i,j] * V[j]); /* ajustada */

solve;

printf "\n========= Accessibility Report ========\n";
printf "Beta: %.2f\n", Beta;
printf "=======================================\n";
printf "Dest\tSupply\tDemand\n";
for {j in J} {
  printf "[%s]\t%d\t%.1f\n", j, S[j], V[j];
}
printf "=======================================\n";
printf "Orig\tAccessibility\n";
for {i in I} {
  printf "[%s]\t%.4f\n", i, Ai[i];
}
printf "=======================================\n\n";

end;
