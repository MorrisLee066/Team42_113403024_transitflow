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


def _driver():
    """Return a Neo4j driver. Caller is responsible for closing."""
    return GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))


def example_count_nodes() -> int:
    """
    Example: count all nodes currently in the graph.
    
    Returns:
        int: Total number of nodes in the graph. Returns 0 if an error occurs.
    """
    try:
        with _driver() as driver:
            with driver.session() as session:
                result = session.run("MATCH (n) RETURN count(n) AS total")
                return result.single()["total"]
    except Exception as e:
        print(f"Neo4j Error in example_count_nodes: {e}")
        return 0


# ── FASTEST ROUTE (Dijkstra by travel_time_min) ───────────────────────────────

def query_shortest_route(
    origin_id: str,
    destination_id: str,
    network: str = "auto",
) -> dict:
    """
    Find the fastest path between two stations, minimising total travel time.
    
    Args:
        origin_id (str): The ID of the starting station.
        destination_id (str): The ID of the destination station.
        network (str): Optional network filter. Defaults to "auto".
        
    Returns:
        dict: A dictionary containing the path, total time, and legs, or {"found": False} if not found or on error.
    """
    try:
        query = """
        MATCH (start {station_id: $origin_id})
        MATCH (end {station_id: $destination_id})
        CALL apoc.algo.dijkstra(start, end, 'METRO_LINK|RAIL_LINK|INTERCHANGE_WITH', 'travel_time_min')
        YIELD path, weight
        RETURN path, weight
        """
        with _driver() as driver:
            with driver.session() as session:
                res = session.run(query, origin_id=origin_id, destination_id=destination_id).single()
                
                if not res or res["path"] is None:
                    return {"found": False}
                
                path = res["path"]
                stations = [{"station_id": n["station_id"], "name": n["name"]} for n in path.nodes]
                legs = [{"type": type(r), "time": r.get("travel_time_min", 0)} for r in path.relationships]
                
                return {
                    "found": True,
                    "origin_id": origin_id,
                    "destination_id": destination_id,
                    "total_time_min": res["weight"],
                    "path": stations,
                    "legs": legs
                }
    except Exception as e:
        print(f"Neo4j Error in query_shortest_route: {e}")
        return {"found": False}


# ── CHEAPEST ROUTE (Dijkstra by fare) ────────────────────────────────────────

def query_cheapest_route(
    origin_id: str,
    destination_id: str,
    network: str = "auto",
    fare_class: str = "standard",
) -> dict:
    """
    Find the cheapest path between two stations, minimising total estimated fare.
    
    Args:
        origin_id (str): The ID of the starting station.
        destination_id (str): The ID of the destination station.
        network (str): Optional network filter. Defaults to "auto".
        fare_class (str): The fare class to use ('standard' or 'first'). Defaults to "standard".
        
    Returns:
        dict: A dictionary containing the path, total fare, and legs, or {"found": False} if not found or on error.
    """
    try:
        weight_property = "fare_first" if fare_class == "first" else "fare"
        
        query = f"""
        MATCH (start {{station_id: $origin_id}})
        MATCH (end {{station_id: $destination_id}})
        CALL apoc.algo.dijkstra(start, end, 'METRO_LINK|RAIL_LINK|INTERCHANGE_WITH', '{weight_property}')
        YIELD path, weight
        RETURN path, weight
        """
        with _driver() as driver:
            with driver.session() as session:
                res = session.run(query, origin_id=origin_id, destination_id=destination_id).single()
                
                if not res or res["path"] is None:
                    return {"found": False}
                
                path = res["path"]
                stations = [{"station_id": n["station_id"], "name": n["name"]} for n in path.nodes]
                legs = [{"type": type(r), "fare": r.get(weight_property, 0)} for r in path.relationships]
                
                return {
                    "found": True,
                    "total_fare_usd": res["weight"],
                    "stations": stations,
                    "legs": legs
                }
    except Exception as e:
        print(f"Neo4j Error in query_cheapest_route: {e}")
        return {"found": False}


# ── ALTERNATIVE ROUTES (avoiding a station) ───────────────────────────────────

def query_alternative_routes(
    origin_id: str,
    destination_id: str,
    avoid_station_id: str,
    network: str = "auto",
    max_routes: int = 3,
) -> list[list[dict]]:
    """
    Find paths between two stations that explicitly avoid a specific intermediate station.
    
    Args:
        origin_id (str): The ID of the starting station.
        destination_id (str): The ID of the destination station.
        avoid_station_id (str): The ID of the station to avoid.
        network (str): Optional network filter. Defaults to "auto".
        max_routes (int): The maximum number of alternative routes to return. Defaults to 3.
        
    Returns:
        list[list[dict]]: A list of routes, where each route is a list of leg dictionaries. Returns [] on error or if none found.
    """
    try:
        query = """
        MATCH path = (start {station_id: $origin_id})-[:METRO_LINK|RAIL_LINK|INTERCHANGE_WITH*1..15]-(end {station_id: $destination_id})
        WHERE NOT any(n IN nodes(path) WHERE n.station_id = $avoid_station_id)
        RETURN path
        ORDER BY length(path) ASC
        LIMIT $max_routes
        """
        routes = []
        with _driver() as driver:
            with driver.session() as session:
                results = session.run(query, origin_id=origin_id, destination_id=destination_id, avoid_station_id=avoid_station_id, max_routes=max_routes)
                for record in results:
                    path = record["path"]
                    legs = [{"from": path.nodes[i]["station_id"], "to": path.nodes[i+1]["station_id"], "type": type(path.relationships[i])} for i in range(len(path.relationships))]
                    routes.append(legs)
        return routes
    except Exception as e:
        print(f"Neo4j Error in query_alternative_routes: {e}")
        return []


# ── CROSS-NETWORK INTERCHANGE PATH ───────────────────────────────────────────

def query_interchange_path(origin_id: str, destination_id: str) -> dict:
    """
    Find the shortest path between a metro station and a national rail station,
    guaranteeing that the path crosses the network boundary via an interchange.
    
    Args:
        origin_id (str): The ID of the starting station.
        destination_id (str): The ID of the destination station.
        
    Returns:
        dict: A dictionary containing the path, interchange points, and total time, or {"found": False} on error.
    """
    try:
        query = """
        MATCH path = shortestPath((start {station_id: $origin_id})-[:METRO_LINK|RAIL_LINK|INTERCHANGE_WITH*]-(end {station_id: $destination_id}))
        WHERE any(r IN relationships(path) WHERE type(r) = 'INTERCHANGE_WITH')
        RETURN path
        """
        with _driver() as driver:
            with driver.session() as session:
                res = session.run(query, origin_id=origin_id, destination_id=destination_id).single()
                if not res or res["path"] is None:
                    return {"found": False}
                    
                path = res["path"]
                stations = [{"station_id": n["station_id"], "name": n["name"]} for n in path.nodes]
                interchange_points = [path.nodes[i]["station_id"] for i, r in enumerate(path.relationships) if type(r) == 'INTERCHANGE_WITH']
                total_time = sum(r.get("travel_time_min", 0) for r in path.relationships)
                
                return {
                    "found": True,
                    "stations": stations,
                    "interchange_points": interchange_points,
                    "total_time_min": total_time
                }
    except Exception as e:
        print(f"Neo4j Error in query_interchange_path: {e}")
        return {"found": False}


# ── DELAY RIPPLE ANALYSIS ─────────────────────────────────────────────────────

def query_delay_ripple(delayed_station_id: str, hops: int = 2) -> list[dict]:
    """
    Find all stations within N hops of a delayed or disrupted station.
    
    Args:
        delayed_station_id (str): The ID of the station experiencing the delay.
        hops (int): The maximum distance (in hops) to calculate the impact radius. Defaults to 2.
        
    Returns:
        list[dict]: A list of affected stations with their distance from the delayed station. Returns [] on error.
    """
    try:
        query = f"""
        MATCH path = (start {{station_id: $delayed_station_id}})-[:METRO_LINK|RAIL_LINK|INTERCHANGE_WITH*1..{hops}]-(affected)
        RETURN DISTINCT affected.station_id AS station_id, affected.name AS name, min(length(path)) AS hops_away
        ORDER BY hops_away ASC
        """
        affected_stations = []
        with _driver() as driver:
            with driver.session() as session:
                results = session.run(query, delayed_station_id=delayed_station_id)
                for record in results:
                    affected_stations.append({
                        "station_id": record["station_id"],
                        "name": record["name"],
                        "hops_away": record["hops_away"],
                        "lines_affected": [] 
                    })
        return affected_stations
    except Exception as e:
        print(f"Neo4j Error in query_delay_ripple: {e}")
        return []


# ── STATION CONNECTIONS ───────────────────────────────────────────────────────

def query_station_connections(station_id: str) -> list[dict]:
    """
    List all direct connections (depth = 1) from a given station.
    
    Args:
        station_id (str): The ID of the target station.
        
    Returns:
        list[dict]: A list of immediately connected stations and their connection types. Returns [] on error.
    """
    try:
        query = """
        MATCH (start {station_id: $station_id})-[r]-(connected)
        RETURN connected.station_id AS station_id, connected.name AS name, type(r) AS connection_type, r.travel_time_min AS time
        ORDER BY time ASC
        """
        connections = []
        with _driver() as driver:
            with driver.session() as session:
                results = session.run(query, station_id=station_id)
                for record in results:
                    connections.append({
                        "connected_station_id": record["station_id"],
                        "connected_station_name": record["name"],
                        "connection_type": record["connection_type"],
                        "travel_time_min": record["time"]
                    })
        return connections
    except Exception as e:
        print(f"Neo4j Error in query_station_connections: {e}")
        return []