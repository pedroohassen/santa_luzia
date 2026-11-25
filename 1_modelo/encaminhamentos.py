# encaminhamentos.py
# Atencao secundaria e terciaria FORA da otimizacao: depois de resolver cada cenario, define para onde
# cada UBS (existente ou nova) encaminha os pacientes de cada tipo de cuidado e mede o acesso.
# Nao influencia a escolha de onde colocar as equipes.
#   - Rede de referencia: aba Estabelecimentos, nivel_modelo 2 e 3, so unidades ativas.
#   - Criterio de referencia: unidade ativa mais proxima (pela rede de ruas) da UBS. Nao ha na base uma
#     pactuacao regional (PDR-MG ou regional de saude) por unidade; por isso nao foi usada.
#   - O paciente viaja da sua casa (centro do setor) ate a unidade de referencia da sua UBS.
#   - Populacao sem cobertura efetiva (situacao atual e C3) e encaminhada pela UBS mais proxima (premissa).
#   - Capacidade dos niveis 2 e 3 NAO avaliada: a base nao tem leitos nem producao (SIA/SIH).
# Saidas: resultados/encaminhamentos.json e resultados/encaminhamentos.xlsx
import json
import os

import pandas as pd

from gera_dados import le_planilha, le_rede
from rede import CENARIOS_REDE, NOME_CENARIO, RES, carrega_rede, dono, rid

# Tipos de cuidado e unidades de referencia (CNES), conforme o pedido do grupo
SERVICOS = {
    "Urgência": ["UPA São Benedito"],
    "Consultas especializadas": ["Centro de Consultas Especializadas I", "Centro de Consultas Especializadas II",
                                 "CTA", "APAE"],
    "Saúde mental": ["CAPS Adulto", "CAPS Infantojuvenil"],
    "Saúde bucal especializada": ["Centro Odontológico Municipal"],
    "Hospitalar": ["Hospital Municipal", "Hospital São João de Deus"],
}
LIMIARES = (5, 10)


def rede_referencia():
    est, _, _ = le_planilha()
    ref = est[est["nivel_modelo"].isin([2, 3]) & (est["situacao"] == "Ativo")]
    fora = est[est["nivel_modelo"].isin([2, 3]) & (est["situacao"] != "Ativo")]["nome"].tolist()
    unidades = {}
    for grupo, nomes in SERVICOS.items():
        for nome in nomes:
            r = ref[ref["nome"] == nome]
            assert len(r) == 1, f"{nome} nao encontrado entre as unidades ativas de nivel 2/3"
            r = r.iloc[0]
            unidades.setdefault(grupo, {})[nome] = f"E{r.name}"   # id na matriz de distancias pela rede
    return unidades, fora


def analisa(rede, servicos):
    inst, unid, aloc = rede["inst"], rede["unidades"], rede["aloc"]
    P, tot = inst["pop"], sum(inst["pop"].values())
    R = le_rede()
    # referencia de cada UBS para cada tipo de cuidado
    matriz = []
    ref = {}
    for j, u in unid.items():
        linha = {"UBS": u["nome"], "nova": u["nova"], "distrito": u["distrito"]}
        for g, us in servicos.items():
            nome, d = min(((n, R[(rid(rede, j), c)]) for n, c in us.items()), key=lambda t: t[1])
            ref[j, g] = nome
            linha[f"{g}: referência"] = nome
            linha[f"{g}: km"] = round(d, 2)
        matriz.append(linha)
    # populacao de cada setor por UBS de referencia (descobertos -> UBS mais proxima)
    fluxo = {}
    for i in inst["S"]:
        fr = {}
        for j, f in aloc.get(i, {}).items():
            fr[dono(inst, j)] = fr.get(dono(inst, j), 0) + f
        resto = 1 - sum(fr.values())
        if resto > 1e-6:
            j = min(unid, key=lambda k: R[(f"S{i}", rid(rede, k))])
            fr[j] = fr.get(j, 0) + resto
        fluxo[i] = fr
    por_servico, populacao_ref = {}, {}
    for g, us in servicos.items():
        dist, pesos = [], []
        for i, fr in fluxo.items():
            for j, f in fr.items():
                nome = ref[j, g]
                populacao_ref[(g, nome)] = populacao_ref.get((g, nome), 0) + P[i] * f
                dist.append(R[(f"S{i}", us[nome])])
                pesos.append(P[i] * f)
        media = sum(d * p for d, p in zip(dist, pesos)) / tot
        # comparacao: ir direto a unidade mais proxima da casa (sem passar pela referencia da UBS)
        direto = sum(P[i] * min(R[(f"S{i}", c)] for c in us.values()) for i in inst["S"]) / tot
        por_servico[g] = {"distancia_media_km": round(media, 2), "distancia_maxima_km": round(max(dist), 2),
                          **{f"pop_acima_{x}km_pct": round(100 * sum(p for d, p in zip(dist, pesos) if d > x) / tot, 1)
                             for x in LIMIARES},
                          "distancia_media_direta_km": round(direto, 2)}
    pop_ref = [{"tipo de cuidado": g, "unidade de referência": n, "população referenciada": round(v),
                "UBS que encaminham": sum(1 for j in unid if ref[j, g] == n)}
               for (g, n), v in sorted(populacao_ref.items())]
    return matriz, por_servico, pop_ref


def main():
    servicos, fora = rede_referencia()
    saida = {"criterio": "unidade ativa de nivel 2/3 mais proxima da UBS (pela rede de ruas)",
             "unidades_desativadas_excluidas": fora,
             "capacidade": "nao avaliada: a base nao tem leitos, producao ambulatorial (SIA) ou hospitalar (SIH)",
             "servicos": {g: list(us) for g, us in servicos.items()}}
    caminho = os.path.join(RES, "encaminhamentos.xlsx")
    with pd.ExcelWriter(caminho) as xw:
        resumo_linhas = []
        for c in CENARIOS_REDE:
            matriz, por_servico, pop_ref = analisa(carrega_rede(c), servicos)
            saida[c] = {"por_servico": por_servico, "populacao_referenciada": pop_ref}
            pd.DataFrame(matriz).to_excel(xw, sheet_name=f"Matriz {c}", index=False)
            pd.DataFrame(pop_ref).to_excel(xw, sheet_name=f"População {c}", index=False)
            for g, v in por_servico.items():
                resumo_linhas.append({"cenário": NOME_CENARIO[c], "tipo de cuidado": g, **v})
            print(NOME_CENARIO[c], {g: (v["distancia_media_km"], v["pop_acima_5km_pct"], v["pop_acima_10km_pct"])
                                    for g, v in por_servico.items()})
        pd.DataFrame(resumo_linhas).to_excel(xw, sheet_name="Resumo", index=False)
    with open(os.path.join(RES, "encaminhamentos.json"), "w", encoding="utf-8") as f:
        json.dump(saida, f, ensure_ascii=False, indent=1)
    print("OK", caminho)


if __name__ == "__main__":
    main()
