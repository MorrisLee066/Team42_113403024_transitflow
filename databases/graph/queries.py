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
    """Example: count all nodes currently in the graph."""
    with _driver() as driver:
        with driver.session() as session:
            result = session.run("MATCH (n) RETURN count(n) AS total")
            return result.single()["total"]


# ── FASTEST ROUTE (Dijkstra by travel_time_min) ───────────────────────────────

def query_shortest_route(
    origin_id: str,
    destination_id: str,
    network: str = "auto",
) -> dict:
    """
    Find the fastest path between two stations, minimising total travel time.
    
    This function utilizes the APOC Dijkstra algorithm to calculate the shortest path
    based on the 'travel_time_min' property defined on the relationships.
    """
    # Cypher Query Explanation:
    # 1. MATCH the starting node (origin) and the ending node (destination) by their station_ids.
    # 2. CALL apoc.algo.dijkstra to find the shortest path.
    #    - The 3rd parameter specifies the allowed relationship types to traverse.
    #    - The 4th parameter specifies the weight property ('travel_time_min') to minimize.
    # 3. YIELD and RETURN the resulting path and the total calculated weight (time).
    query = """
    MATCH (start {station_id: $origin_id})
    MATCH (end {station_id: $destination_id})
    CALL apoc.algo.dijkstra(start, end, 'METRO_LINK|RAIL_LINK|INTERCHANGE_TO|INTERCHANGE_WITH', 'travel_time_min')
    YIELD path, weight
    RETURN path, weight
    """
    with _driver() as driver:
        with driver.session() as session:
            res = session.run(query, origin_id=origin_id, destination_id=destination_id).single()
            
            # If no path is found or the result is empty, return a dictionary indicating failure.
            if not res or res["path"] is None:
                return {"found": False}
            
            path = res["path"]
            
            # Extract node information (stations) from the path object.
            stations = [{"station_id": n["station_id"], "name": n["name"]} for n in path.nodes]
            
            # Extract relationship information (legs of the journey) from the path object.
            # Using type(r) captures whether it's a METRO_LINK, RAIL_LINK, etc.
            legs = [{"type": type(r), "time": r.get("travel_time_min", 0)} for r in path.relationships]
            
            return {
                "found": True,
                "origin_id": origin_id,
                "destination_id": destination_id,
                "total_time_min": res["weight"], # This is the total travel time computed by Dijkstra
                "path": stations,
                "legs": legs
            }


# ── CHEAPEST ROUTE (Dijkstra by fare) ────────────────────────────────────────

def query_cheapest_route(
    origin_id: str,
    destination_id: str,
    network: str = "auto",
    fare_class: str = "standard",
) -> dict:
    """
    Find the cheapest path between two stations, minimising total estimated fare.
    
    Similar to the shortest route, but dynamically assigns the weight property
    based on the requested fare class ('standard' vs 'first' class).
    """
    # Determine which property on the relationship represents the cost to minimize.
    weight_property = "fare_first" if fare_class == "first" else "fare"
    
    # We dynamically inject the weight_property into the APOC call using an f-string.
    query = f"""
    MATCH (start {{station_id: $origin_id}})
    MATCH (end {{station_id: $destination_id}})
    CALL apoc.algo.dijkstra(start, end, 'METRO_LINK|RAIL_LINK|INTERCHANGE_TO|INTERCHANGE_WITH', '{weight_property}')
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
                "total_fare_usd": res["weight"], # This is the total fare computed by Dijkstra
                "stations": stations,
                "legs": legs
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
    Find paths between two stations that explicitly avoid a specific intermediate station.
    Useful for rerouting passengers when a station is closed or delayed.
    """
    # Cypher Query Explanation:
    # 1. MATCH variable-length paths (up to 15 hops) between origin and destination.
    # 2. WHERE NOT any(...) acts as a strict filter: it scans all nodes within the matched path
    #    and discards the path entirely if the 'avoid_station_id' is found among them.
    # 3. ORDER BY length(path) ASC ensures we return the shortest alternative paths first.
    # 4. LIMIT the results to prevent massive result sets and performance issues.
    query = """
    MATCH path = (start {station_id: $origin_id})-[:METRO_LINK|RAIL_LINK|INTERCHANGE_TO|INTERCHANGE_WITH*1..15]-(end {station_id: $destination_id})
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
                
                # Construct a list of leg dictionaries representing the step-by-step journey.
                legs = [{"from": path.nodes[i]["station_id"], "to": path.nodes[i+1]["station_id"], "type": type(path.relationships[i])} for i in range(len(path.relationships))]
                routes.append(legs)
    return routes


# ── CROSS-NETWORK INTERCHANGE PATH ───────────────────────────────────────────

def query_interchange_path(origin_id: str, destination_id: str) -> dict:
    """
    Find the shortest path between a metro station and a national rail station,
    guaranteeing that the path crosses the network boundary via an interchange.
    """
    # Cypher Query Explanation:
    # 1. Use the shortestPath() built-in function to find the most direct route.
    # 2. The WHERE clause enforces a condition on the relationships: at least one relationship
    #    in the path MUST be an 'INTERCHANGE_TO' or 'INTERCHANGE_WITH' type. This ensures
    #    we are actively crossing networks.
    query = """
    MATCH path = shortestPath((start {station_id: $origin_id})-[:METRO_LINK|RAIL_LINK|INTERCHANGE_TO|INTERCHANGE_WITH*]-(end {station_id: $destination_id}))
    WHERE any(r IN relationships(path) WHERE type(r) IN ['INTERCHANGE_TO', 'INTERCHANGE_WITH'])
    RETURN path
    """
    with _driver() as driver:
        with driver.session() as session:
            res = session.run(query, origin_id=origin_id, destination_id=destination_id).single()
            if not res or res["path"] is None:
                return {"found": False}
                
            path = res["path"]
            stations = [{"station_id": n["station_id"], "name": n["name"]} for n in path.nodes]
            
            # Identify the specific stations where the user needs to transfer.
            # We map over the relationships and grab the corresponding node ID if the relationship type indicates an interchange.
            interchange_points = [path.nodes[i]["station_id"] for i, r in enumerate(path.relationships) if type(r) in ['INTERCHANGE_TO', 'INTERCHANGE_WITH']]
            
            # Calculate the total time by summing up the travel_time_min of all relationships in the path.
            total_time = sum(r.get("travel_time_min", 0) for r in path.relationships)
            
            return {
                "found": True,
                "stations": stations,
                "interchange_points": interchange_points,
                "total_time_min": total_time
            }


# ── DELAY RIPPLE ANALYSIS ─────────────────────────────────────────────────────

def query_delay_ripple(delayed_station_id: str, hops: int = 2) -> list[dict]:
    """
    Find all stations within N hops of a delayed or disrupted station.
    This dynamically calculates the potential impact radius of an incident.
    """
    # Cypher Query Explanation:
    # 1. MATCH a variable-length path spreading outwards from the delayed station.
    #    The `*1..{hops}` syntax dynamically sets the maximum depth of the search based on the function argument.
    # 2. RETURN DISTINCT ensures we don't list the same affected station multiple times if reached via different paths.
    # 3. min(length(path)) calculates the shortest distance (in hops) from the delayed station to the affected station.
    query = f"""
    MATCH path = (start {{station_id: $delayed_station_id}})-[:METRO_LINK|RAIL_LINK|INTERCHANGE_TO|INTERCHANGE_WITH*1..{hops}]-(affected)
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
                    # lines_affected is a placeholder here. In a production system, 
                    # you might extract the 'line' property from the traversed relationships to populate this.
                    "lines_affected": [] 
                })
    return affected_stations


# ── STATION CONNECTIONS ───────────────────────────────────────────────────────

def query_station_connections(station_id: str) -> list[dict]:
    """
    List all direct connections (depth = 1) from a given station.
    Useful for displaying departure boards or immediate travel options.
    """
    # Cypher Query Explanation:
    # 1. MATCH the starting node and any node strictly exactly 1 hop away (using `-[r]-`).
    # 2. RETURN the connected station's details, the type of connection (e.g., METRO_LINK), 
    #    and the travel time.
    # 3. ORDER BY time ASC to show the closest/fastest connections first.
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