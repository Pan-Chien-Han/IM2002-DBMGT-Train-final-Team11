"""
TransitFlow — Neo4j Graph Database Layer
=========================================
This module handles all queries to Neo4j.

GRAPH ROLE:
  - Model the dual transit network (city metro M1–M4 + national rail NR1–NR2)
  - Find fastest routes (Dijkstra by travel_time_min via APOC)
  - Find cheapest routes (Dijkstra by fare via APOC)
  - Find alternative routes avoiding a given station
  - Find cross-network interchange paths (metro → rail or rail → metro)
  - Show delay ripple: which stations are affected within N hops

STUDENT TASK
------------
Design your graph schema (node labels, relationship types, properties)
based on the data in train-mock-data/, seed it with skeleton/seed_neo4j.py,
then implement the query_ functions below.

Functions prefixed with `query_` are called by the agent (skeleton/agent.py).
"""

from __future__ import annotations

from typing import Optional

from neo4j import GraphDatabase

from skeleton.config import NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD

# ── 老師給的原始碼（保持原封不動，最安全） ───────────────────────────────────────

def _driver():
    """Return a Neo4j driver. Caller is responsible for closing."""
    return GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))


# ── Example ───────────────────────────────────────────────────────────────────

def example_count_nodes() -> int:
    """Example: count all nodes currently in the graph."""
    with _driver() as driver:
        with driver.session() as session:
            result = session.run("MATCH (n) RETURN count(n) AS total")
            return result.single()["total"]


# ── 業界生產環境優化：全域單例驅動程式 (Singleton Driver) ─────────────────────────
# 獨立建立一個全域共享的連線池，完美符合講義精神！
_PROD_DRIVER = GraphDatabase.driver(
    NEO4J_URI, 
    auth=(NEO4J_USER, NEO4J_PASSWORD),
    max_connection_pool_size=50
)


# ── FASTEST ROUTE (Dijkstra by travel_time_min) ───────────────────────────────

def query_shortest_route(
    origin_id: str,
    destination_id: str,
    network: str = "auto",
) -> dict:
    """
    找出兩個車站之間最快速的路徑（將總行車時間降到最低）。
    """
    if network == "auto":
        start_label = "MetroStation" if origin_id.startswith("MS") else "NationalRailStation"
        end_label = "MetroStation" if destination_id.startswith("MS") else "NationalRailStation"
    else:
        start_label = "MetroStation" if network == "metro" else "NationalRailStation"
        end_label = "MetroStation" if network == "metro" else "NationalRailStation"

    cypher = f"""
    MATCH (start:{start_label} {{station_id: $origin_id}})
    MATCH (end:{end_label} {{station_id: $destination_id}})
    CALL apoc.algo.dijkstra(start, end, 'LINK_TO', 'travel_time_min')
    YIELD path, weight
    RETURN path, weight
    """

    with _PROD_DRIVER.session() as session:
        result = session.run(cypher, origin_id=origin_id, destination_id=destination_id)
        record = result.single()

        if not record:
            return {
                "found": False,
                "origin_id": origin_id,
                "destination_id": destination_id,
                "total_time_min": 0,
                "path": [],
                "legs": []
            }

        path_obj = record["path"]
        total_time = record["weight"]

        stations_list = []
        for node in path_obj.nodes:
            stations_list.append({
                "station_id": node["station_id"],
                "name": node["name"],
                "lines": node["lines"]
            })

        legs_list = []
        for rel in path_obj.relationships:
            legs_list.append({
                "line": rel["line"],
                "travel_time_min": rel["travel_time_min"]
            })

        return {
            "found": True,
            "origin_id": origin_id,
            "destination_id": destination_id,
            "total_time_min": total_time,
            "path": stations_list,
            "legs": legs_list
        }


# ── CHEAPEST ROUTE (Dijkstra by fare) ────────────────────────────────────────

def query_cheapest_route(
    origin_id: str,
    destination_id: str,
    network: str = "auto",
    fare_class: str = "standard",
) -> dict:
    """
    找出兩個車站之間最划算、票價總和最低的路徑。
    """
    if network == "auto":
        start_label = "MetroStation" if origin_id.startswith("MS") else "NationalRailStation"
        end_label = "MetroStation" if destination_id.startswith("MS") else "NationalRailStation"
    else:
        start_label = "MetroStation" if network == "metro" else "NationalRailStation"
        end_label = "MetroStation" if network == "metro" else "NationalRailStation"

    if start_label == "MetroStation":
        fare_property = "fare"
    else:
        fare_property = "first_fare_usd" if fare_class == "first" else "standard_fare_usd"

    cypher = f"""
    MATCH (start:{start_label} {{station_id: $origin_id}})
    MATCH (end:{end_label} {{station_id: $destination_id}})
    CALL apoc.algo.dijkstra(start, end, 'LINK_TO', $fare_property)
    YIELD path, weight
    RETURN path, weight
    """

    with _PROD_DRIVER.session() as session:
        result = session.run(
            cypher, 
            origin_id=origin_id, 
            destination_id=destination_id, 
            fare_property=fare_property
        )
        record = result.single()

        if not record:
            return {
                "found": False,
                "total_fare_usd": 0.0,
                "stations": [],
                "legs": []
            }

        path_obj = record["path"]
        total_fare = record["weight"]

        stations_list = []
        for node in path_obj.nodes:
            stations_list.append({
                "station_id": node["station_id"],
                "name": node["name"],
                "lines": node["lines"]
            })

        legs_list = []
        for rel in path_obj.relationships:
            legs_list.append({
                "line": rel["line"],
                "fare": rel.get(fare_property, 0.0)
            })

        return {
            "found": True,
            "total_fare_usd": float(total_fare),
            "stations": stations_list,
            "legs": legs_list
        }


# ── ALTERNATIVE ROUTES (avoiding a station) ───────────────────────────────────

def query_alternative_routes(
    origin_id: str,
    destination_id: str,
    avoid_station_id: str,
    network: str = "auto",
    max_routes: int = 3,
) -> list[list[dict]]:
    """
    找出兩個車站之間，避開特定故障車站（avoid_station_id）的替代路線。
    """
    if network == "auto":
        start_label = "MetroStation" if origin_id.startswith("MS") else "NationalRailStation"
        end_label = "MetroStation" if destination_id.startswith("MS") else "NationalRailStation"
    else:
        start_label = "MetroStation" if network == "metro" else "NationalRailStation"
        end_label = "MetroStation" if network == "metro" else "NationalRailStation"

    cypher = f"""
    MATCH path = (start:{start_label})-[:LINK_TO*..10]->(end:{end_label})
    WHERE start.station_id = $origin_id 
      AND end.station_id = $destination_id
      AND NONE(node IN nodes(path)[1..-1] WHERE node.station_id = $avoid_station_id)
    RETURN path
    ORDER BY length(path) ASC
    LIMIT $max_routes
    """

    routes_list = []
    with _PROD_DRIVER.session() as session:
        result = session.run(
            cypher, 
            origin_id=origin_id, 
            destination_id=destination_id, 
            avoid_station_id=avoid_station_id,
            max_routes=max_routes
        )

        for record in result:
            path_obj = record["path"]
            current_route_legs = []
            
            for rel in path_obj.relationships:
                current_route_legs.append({
                    "line": rel["line"],
                    "from_station_id": rel.start_node["station_id"],
                    "to_station_id": rel.end_node["station_id"],
                    "travel_time_min": rel.get("travel_time_min", 0)
                })
            routes_list.append(current_route_legs)

    return routes_list


# ── CROSS-NETWORK INTERCHANGE PATH ───────────────────────────────────────────

def query_interchange_path(origin_id: str, destination_id: str) -> dict:
    """
    找出跨越捷運與國家鐵路網絡邊界的跨系統雙軌轉乘最佳路徑。
    """
    cypher = """
    MATCH path = (start)-[:LINK_TO|INTERCHANGE_WITH*..15]->(end)
    WHERE start.station_id = $origin_id AND end.station_id = $destination_id
    RETURN path, 
           reduce(total_time = 0, r IN relationships(path) | 
               total_time + coalesce(r.travel_time_min, r.transfer_time_min, 0)
           ) AS total_time
    ORDER BY total_time ASC
    LIMIT 1
    """

    with _PROD_DRIVER.session() as session:
        result = session.run(cypher, origin_id=origin_id, destination_id=destination_id)
        record = result.single()

        if not record:
            return {"found": False, "stations": [], "interchange_points": [], "total_time_min": 0}

        path_obj = record["path"]
        total_time = record["total_time"]

        stations_list = []
        for node in path_obj.nodes:
            stations_list.append({
                "station_id": node["station_id"],
                "name": node["name"],
                "type": list(node.labels)[0]
            })

        interchanges = []
        for rel in path_obj.relationships:
            if rel.type == "INTERCHANGE_WITH":
                interchanges.append({
                    "from_station_id": rel.start_node["station_id"],
                    "to_station_id": rel.end_node["station_id"],
                    "transfer_time_min": rel["transfer_time_min"]
                })

        return {
            "found": True,
            "stations": stations_list,
            "interchange_points": interchanges,
            "total_time_min": int(total_time)
        }


# ── DELAY RIPPLE ANALYSIS ─────────────────────────────────────────────────────

def query_delay_ripple(delayed_station_id: str, hops: int = 2) -> list[dict]:
    """
    尋找因突發延誤而受到波及的 N 站以內所有鄰近車站。
    """
    cypher = """
    MATCH path = (start)-[:LINK_TO*..15]-(affected)
    WHERE start.station_id = $delayed_station_id AND start <> affected
    WITH affected, min(length(path)) AS shortest_hop
    WHERE shortest_hop <= $hops
    RETURN affected.station_id AS station_id, 
           affected.name AS name, 
           shortest_hop AS hops_away, 
           affected.lines AS lines_affected
    ORDER BY hops_away ASC
    """

    ripple_list = []
    with _PROD_DRIVER.session() as session:
        result = session.run(cypher, delayed_station_id=delayed_station_id, hops=hops)
        for record in result:
            ripple_list.append({
                "station_id": record["station_id"],
                "name": record["name"],
                "hops_away": record["hops_away"],
                "lines_affected": record["lines_affected"]
            })
    return ripple_list


# ── STATION CONNECTIONS ───────────────────────────────────────────────────────

def query_station_connections(station_id: str) -> list[dict]:
    """
    列出一個指定車站所有直接相連的下一站與其線路。
    """
    cypher = """
    MATCH (start {station_id: $station_id})-[r:LINK_TO]->(next)
    RETURN next.station_id AS station_id, next.name AS name, r.line AS line
    """

    connections = []
    with _PROD_DRIVER.session() as session:
        result = session.run(cypher, station_id=station_id)
        for record in result:
            connections.append({
                "station_id": record["station_id"],
                "name": record["name"],
                "line": record["line"]
            })
    return connections
