# volante.py
# Cenario C3 (analise de sensibilidade sobre a meta): a solucao otima cobre >= 99% da populacao; o restante
# e atendido por uma eSF volante. Desenho FORA da otimizacao: quem fica de fora, UBS-sede, pontos de
# atendimento, cronograma mensal, demanda por dia, composicao e custo anual.
# Saidas: resultados/C3/volante.json, volante_pontos.csv e volante_cronograma.csv
#
# Premissas (declaradas no relatorio):
#   - Ponto de atendimento: ponto de apoio rural existente quando houver um a ate RAIO_APOIO km (pela rua) do
#     setor; senao, um local no proprio setor (centro do setor; o imovel exato fica a definir).
#   - UBS-sede: a UBS (existente ou nova do C3) que minimiza o deslocamento total da equipe (pela rua),
#     ponderado pela populacao de cada ponto.
#   - Jornada: eSF de 40 h semanais (PNAB), 5 dias por semana, DIAS_MES dias uteis por mes; um ponto por dia;
#     dias por ponto proporcionais a populacao, com no minimo MIN_DIAS dias por mes (1, se nao couber).
#   - Cronograma: as visitas de cada ponto sao espalhadas ao longo do mes (intervalos regulares).
#   - Deslocamento: ida e volta sede-ponto em cada dia de atendimento, pela rede de ruas (OpenStreetMap).
#   - Demanda esperada: consultas medicas basicas por habitante por ano (Portaria GM/MS n. 1.101/2002:
#     2 a 3 consultas medicas/hab/ano, 63% basicas; valor central 1,575).
#   - Custo por km (veiculo): Central de Compras/Ministerio da Economia - frota locada R$ 9,04/km (central);
#     testados TaxiGov R$ 3,88/km e frota propria R$ 9,58/km (valores nominais, sem correcao).
#   - Custeio da equipe: custo medio da APS por eSF (despesa APS 2025 / 57 eSF, teto) e repasse federal da planilha
#     (so se a equipe for credenciada como eSF; a eSF volante nao tem enquadramento normativo especifico).
import csv
import json
import math
import os

from gera_dados import le_rede
from rede import RES, carrega_rede

RAIO_APOIO = 3.0
DIAS_MES = 21
MIN_DIAS = 2
VISITAS_ANO = 2.5 * 0.63
JORNADA_MIN = 8 * 60
CUSTO_KM = {"frota locada (central)": 9.04, "TáxiGov (inferior)": 3.88, "frota própria (superior)": 9.58}
FONTE_KM = ("Central de Compras – Ministério da Economia, 'Dados sobre transporte de pessoas da "
            "Administração Pública Federal' (custo por km rodado)")
DIAS_SEMANA = ["segunda", "terça", "quarta", "quinta", "sexta"]


def ler_csv(caminho):
    with open(caminho, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f, delimiter=";"))


def distribui_dias(pontos, dias_total, minimo):
    """Dias por mes proporcionais a populacao, com minimo por ponto, somando dias_total."""
    tot = sum(p["populacao"] for p in pontos.values())
    bruto = {k: max(minimo, dias_total * p["populacao"] / tot) for k, p in pontos.items()}
    escala = dias_total / sum(bruto.values())
    dias = {k: max(minimo, round(v * escala)) for k, v in bruto.items()}
    while sum(dias.values()) > dias_total:
        k = max((k for k in dias if dias[k] > minimo), key=lambda k: dias[k] - bruto[k] * escala)
        dias[k] -= 1
    while sum(dias.values()) < dias_total:
        k = max(dias, key=lambda k: bruto[k] * escala - dias[k])
        dias[k] += 1
    return dias


def cronograma(dias, n_dias):
    """Atribui as visitas de cada ponto a dias do mes, espalhadas em intervalos regulares (um ponto por dia)."""
    livres = list(range(n_dias))
    agenda = {}
    for k in sorted(dias, key=lambda k: -dias[k]):            # pontos com mais visitas primeiro
        passo = n_dias / dias[k]
        for v in range(dias[k]):
            alvo = (v + 0.5) * passo
            d = min(livres, key=lambda x: abs(x - alvo))
            livres.remove(d)
            agenda[d] = k
    return agenda


def desenho(pontos, dias_total, n_equipes, minimo, dist_sede):
    dias = distribui_dias(pontos, dias_total, minimo)
    agenda = cronograma(dias, dias_total)
    km_mes = sum(2 * dist_sede[agenda[d]] for d in agenda)
    return dias, agenda, km_mes


def main():
    rede = carrega_rede("C3")
    inst, unid = rede["inst"], rede["unidades"]
    pt = inst["pontos"]
    R = le_rede()
    resumo = rede["resumo"]
    fin = resumo["financeiro"]
    desc = ler_csv(os.path.join(RES, "C3", "descobertos.csv"))
    tot_v = sum(float(d["populacao_descoberta"]) for d in desc)
    rural_v = sum(float(d["populacao_descoberta"]) for d in desc if d["fora_area_urbanizada"] == "1")

    def rid(j):   # id do ponto na matriz de distancias pela rede
        return pt[j]["rede"] if j in pt else f"S{unid[j]['setor']}"

    # pontos de atendimento
    pontos = {}
    for d in desc:
        i = d["setor"]
        apoio = min(inst["A"], key=lambda a: R[(f"S{i}", pt[a]["rede"])])
        da = R[(f"S{i}", pt[apoio]["rede"])]
        if d["fora_area_urbanizada"] == "1" and da <= RAIO_APOIO:
            chave, nome, lat, lon, ref = apoio, pt[apoio]["nome"], pt[apoio]["lat"], pt[apoio]["lon"], pt[apoio]["rede"]
        else:
            chave, nome = f"SETOR_{i}", f"Local no setor {i}"
            (lat, lon), ref = inst["coord_s"][i], f"S{i}"
        p = pontos.setdefault(chave, {"ponto": nome, "lat": lat, "lon": lon, "rede": ref, "populacao": 0.0, "setores": []})
        p["populacao"] += float(d["populacao_descoberta"])
        p["setores"].append(i)

    # UBS-sede: minimiza o deslocamento total (pela rua) ponderado pela populacao
    def custo_sede(j):
        return sum(p["populacao"] * R[(rid(j), p["rede"])] for p in pontos.values())
    sede = min(unid, key=custo_sede)
    dist_sede = {k: R[(rid(sede), p["rede"])] for k, p in pontos.items()}

    n_equipes = math.ceil(tot_v / inst["cap"])
    dias_total = DIAS_MES * n_equipes
    minimo = MIN_DIAS if len(pontos) * MIN_DIAS <= dias_total else 1
    dias, agenda, km_mes = desenho(pontos, dias_total, n_equipes, minimo, dist_sede)

    linhas = []
    for k, p in sorted(pontos.items(), key=lambda kv: -kv[1]["populacao"]):
        consultas_mes = p["populacao"] * VISITAS_ANO / 12
        por_dia = consultas_mes / dias[k]
        linhas.append({"ponto": p["ponto"], "setores": " ".join(p["setores"]), "populacao": round(p["populacao"]),
                       "dist_sede_rede_km": round(dist_sede[k], 2), "dias_por_mes": dias[k],
                       "dias_do_mes": " ".join(str(d + 1) for d in sorted(x for x in agenda if agenda[x] == k)),
                       "consultas_medicas_mes": round(consultas_mes, 1), "consultas_por_dia_de_visita": round(por_dia, 1),
                       "minutos_por_consulta_na_jornada": round(JORNADA_MIN / por_dia) if por_dia else None,
                       "km_mes": round(2 * dist_sede[k] * dias[k], 1), "lat": p["lat"], "lon": p["lon"]})
    crono = []
    for d in range(dias_total):
        k = agenda[d]
        crono.append({"dia_util": d + 1, "semana": d // 5 + 1, "dia_semana": DIAS_SEMANA[d % 5],
                      "ponto": pontos[k]["ponto"], "km_ida_e_volta": round(2 * dist_sede[k], 1)})
    km_ano = 12 * km_mes

    # variante: cada ponto visitado ao menos 1 vez por semana (4 dias/mes) -> quantas equipes volantes
    dias_sem = sum(max(4, dias[k]) for k in pontos)
    n_sem = math.ceil(dias_sem / DIAS_MES)
    dias_v, _, km_mes_v = desenho(pontos, DIAS_MES * n_sem, n_sem, 4, dist_sede)

    def custos(n_eq, km_a):
        bruto, rep = n_eq * fin["custo_medio_aps_por_esf_ano_R$"], n_eq * fin["repasse_federal_por_esf_ano_R$"]
        veic = {kk: round(km_a * v) for kk, v in CUSTO_KM.items()}
        return bruto, rep, veic

    custeio_bruto, repasse, veiculo = custos(n_equipes, km_ano)
    central = veiculo["frota locada (central)"]
    bruto_s, _, veic_s = custos(n_sem, 12 * km_mes_v)
    obras, liq_esf = resumo["indicadores"]["investimento_obras_R$"], fin["custeio_liquido_municipio_ano_R$"]
    total = {
        "obras_R$": obras,
        "custeio_liquido_esf_novas_ano_R$": liq_esf,
        "volante_custeio_bruto_ano_R$": custeio_bruto,
        "volante_veiculo_ano_R$": central,
        "volante_total_sem_repasse_ano_R$": custeio_bruto + central,
        "volante_total_com_repasse_ano_R$": custeio_bruto - repasse + central,
        "esforco_anual_sem_repasse_volante_R$": round(liq_esf + obras / 4 + custeio_bruto + central),
        "esforco_anual_com_repasse_volante_R$": round(liq_esf + obras / 4 + custeio_bruto - repasse + central),
    }
    aps = resumo["referencias_financeiras"]["despesa_aps_anual"]
    total["esforco_sem_repasse_pct_despesa_APS"] = round(100 * total["esforco_anual_sem_repasse_volante_R$"] / aps, 1)
    total["esforco_com_repasse_pct_despesa_APS"] = round(100 * total["esforco_anual_com_repasse_volante_R$"] / aps, 1)
    consultas_ano = tot_v * VISITAS_ANO
    total["custo_por_consulta_esperada_R$"] = round((custeio_bruto + central) / consultas_ano) if consultas_ano else None

    saida = {
        "populacao_descoberta": round(tot_v), "populacao_descoberta_fora_area_urbanizada": round(rural_v),
        "populacao_descoberta_area_urbanizada": round(tot_v) - round(rural_v),
        "setores_descobertos": len(desc),
        "setores": [{k: d[k] for k in ("setor", "distrito", "situacao_ibge", "populacao_setor", "populacao_descoberta",
                                        "ubs_mais_proxima", "distancia_ubs_mais_proxima_km")} for d in desc],
        "n_equipes_volantes": n_equipes,
        "ubs_sede": unid[sede]["nome"], "ubs_sede_id": sede,
        "ubs_sede_lat": unid[sede]["lat"], "ubs_sede_lon": unid[sede]["lon"],
        "pontos": linhas, "cronograma": crono, "km_ano": round(km_ano),
        "consultas_medicas_esperadas_ano": round(consultas_ano),
        "variante_visita_semanal": {"n_equipes_volantes": n_sem, "dias_por_ponto": {pontos[k]["ponto"]: v for k, v in dias_v.items()},
                                    "km_ano": round(12 * km_mes_v), "custeio_bruto_ano_R$": bruto_s,
                                    "veiculo_ano_R$": veic_s["frota locada (central)"],
                                    "total_sem_repasse_ano_R$": bruto_s + veic_s["frota locada (central)"]},
        "composicao": ["médico(a)", "enfermeiro(a)", "técnico(a) ou auxiliar de enfermagem",
                       "agente(s) comunitário(s) de saúde dos setores atendidos", "motorista (no custo do veículo)"],
        "jornada": "40 h semanais (PNAB), 5 dias por semana, 8 h por dia, um ponto por dia",
        "custo_veiculo_ano_R$": veiculo,
        "custos": total,
        "premissas": {"raio_ponto_de_apoio_km": RAIO_APOIO, "dias_uteis_mes": DIAS_MES, "minimo_dias_mes": MIN_DIAS,
                      "minimo_dias_aplicado": minimo, "distancia": "rede de ruas (OpenStreetMap)",
                      "visitas_ano": VISITAS_ANO, "custo_km_R$": CUSTO_KM, "fonte_custo_km": FONTE_KM,
                      "custo_equipe": "custo medio da APS por eSF (teto), RAG 2025 / 57 eSF",
                      "repasse": "R$ 24 mil/mes por eSF (planilha), so se credenciada"},
    }
    with open(os.path.join(RES, "C3", "volante.json"), "w", encoding="utf-8") as f:
        json.dump(saida, f, ensure_ascii=False, indent=1)
    for nome, dados in (("volante_pontos.csv", linhas), ("volante_cronograma.csv", crono)):
        with open(os.path.join(RES, "C3", nome), "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(dados[0]), delimiter=";")
            w.writeheader()
            w.writerows(dados)
    print(json.dumps({k: v for k, v in saida.items() if k not in ("setores", "cronograma")}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
