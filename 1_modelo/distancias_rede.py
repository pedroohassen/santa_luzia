# distancias_rede.py
# Distancia pelo trajeto efetivo (rede de ruas do OpenStreetMap, rede "a pe") entre os centros dos
# setores censitarios e os estabelecimentos de saude de Santa Luzia.
#
# Metodo: a rede de vias do municipio (com 1,5 km de margem) e baixada com o OSMnx; cada ponto (centro de
# setor ou estabelecimento) e ligado ao no mais proximo da maior componente conexa da rede; a distancia
# entre dois pontos = trecho em linha reta do ponto ate a rede + caminho minimo na rede (Dijkstra) + trecho
# da rede ate o outro ponto. O resultado fica salvo em distancias_rede.csv (formato longo) e em
# matriz_distancias_rede.xlsx (aba "Matriz completa (km)"), na pasta de dados; os demais scripts leem esse arquivo (nao precisa da internet de novo).
#
# Uso: python distancias_rede.py            (precisa de internet apenas na primeira vez)
import json
import os
import time

import networkx as nx
import osmnx as ox
import pandas as pd
from shapely.geometry import shape

from gera_dados import DADOS, PLANILHA, hav

GRAFO = os.path.join(DADOS, "rede_osm_pe.graphml")
SAIDA_CSV = os.path.join(DADOS, "distancias_rede.csv")
SAIDA_XLSX = os.path.join(DADOS, "matriz_distancias_rede.xlsx")


def pontos():
    """id -> (lat, lon, rotulo). Setores: 'S<cd_setor>'; estabelecimentos com coordenada: 'E<linha>'."""
    st = pd.read_excel(PLANILHA, sheet_name="Setores")
    es = pd.read_excel(PLANILHA, sheet_name="Estabelecimentos")
    p = {}
    for _, r in st.iterrows():
        p[f"S{int(r['cd_setor'])}"] = (float(r["latitude"]), float(r["longitude"]), f"setor {int(r['cd_setor'])}")
    for k, r in es.iterrows():
        if pd.notna(r["latitude"]) and pd.notna(r["longitude"]):
            p[f"E{k}"] = (float(r["latitude"]), float(r["longitude"]), r["nome"])
    return p


def baixa_rede():
    if os.path.exists(GRAFO):
        return ox.load_graphml(GRAFO)
    gj = json.load(open(os.path.join(DADOS, "limite_municipal_ibge.geojson"), encoding="utf-8"))
    geo = gj["features"][0]["geometry"] if "features" in gj else gj.get("geometry", gj)
    poli = shape(geo).buffer(0.015)            # ~1,5 km de margem (graus)
    g = ox.graph_from_polygon(poli, network_type="walk", simplify=True)
    ox.save_graphml(g, GRAFO)
    return g


def main():
    t0 = time.time()
    g = baixa_rede()
    g = ox.convert.to_undirected(g)
    g = g.subgraph(max(nx.connected_components(g), key=len)).copy()
    print(f"rede: {g.number_of_nodes()} nos, {g.number_of_edges()} arestas ({time.time() - t0:.0f} s)")
    P = pontos()
    ids = list(P)
    lat = [P[i][0] for i in ids]
    lon = [P[i][1] for i in ids]
    nos = ox.distance.nearest_nodes(g, lon, lat)
    no = dict(zip(ids, nos))
    snap = {i: hav(P[i][0], P[i][1], g.nodes[no[i]]["y"], g.nodes[no[i]]["x"]) for i in ids}
    alvos = {}
    for i in ids:
        alvos.setdefault(no[i], []).append(i)
    linhas = []
    for k, i in enumerate(ids):
        comp = nx.single_source_dijkstra_path_length(g, no[i], weight="length")
        for n_alvo, js in alvos.items():
            if n_alvo not in comp:
                continue
            for j in js:
                d = 0.0 if i == j else snap[i] + comp[n_alvo] / 1000.0 + snap[j]
                linhas.append((i, j, round(d, 4)))
        if k % 50 == 0:
            print(f"  origem {k + 1}/{len(ids)} ({time.time() - t0:.0f} s)")
    df = pd.DataFrame(linhas, columns=["origem", "destino", "dist_rede_km"])
    df["dist_reta_km"] = [round(hav(P[a][0], P[a][1], P[b][0], P[b][1]), 4) for a, b in zip(df.origem, df.destino)]
    df.to_csv(SAIDA_CSV, index=False, sep=";", encoding="utf-8")
    # Planilha: setores x estabelecimentos (rede) + resumo da conexao de cada ponto a rede
    est = [i for i in ids if i.startswith("E")]
    wide = df[df.origem.str.startswith("S") & df.destino.isin(est)].pivot(index="origem", columns="destino", values="dist_rede_km")
    wide.columns = [P[c][2] for c in wide.columns]
    wide.index = [i[1:] for i in wide.index]
    wide.index.name = "cd_setor"
    resumo = pd.DataFrame({"ponto": ids, "nome": [P[i][2] for i in ids], "no_osm": [no[i] for i in ids],
                           "conexao_ate_rede_km": [round(snap[i], 4) for i in ids]})
    with pd.ExcelWriter(SAIDA_XLSX) as w:
        pd.DataFrame({"campo": ["metodo", "rede", "fonte", "distancia", "unidade"],
                      "descricao": ["caminho minimo (Dijkstra) na rede de vias", "OpenStreetMap, rede a pe (OSMnx)",
                                    "© colaboradores do OpenStreetMap (ODbL)",
                                    "ligacao do ponto a rede + caminho na rede + ligacao da rede ao destino", "km"]}
                     ).to_excel(w, sheet_name="Leia-me", index=False)
        wide.round(3).to_excel(w, sheet_name="Setores x estabelecimentos")
        resumo.to_excel(w, sheet_name="Conexao a rede", index=False)
        # matriz completa (setores S<codigo> e estabelecimentos E<linha>), lida pelo gera_dados.py
        df.pivot(index="origem", columns="destino", values="dist_rede_km").reindex(index=ids, columns=ids).to_excel(
            w, sheet_name="Matriz completa (km)")
    r = df[(df.dist_reta_km > 0.3)]
    print(f"OK: {len(df)} pares | razao rede/reta (pares > 0,3 km): mediana {(r.dist_rede_km / r.dist_reta_km).median():.3f}, "
          f"media {(r.dist_rede_km / r.dist_reta_km).mean():.3f} | conexao media a rede {sum(snap.values()) / len(snap) * 1000:.0f} m "
          f"| {time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()
