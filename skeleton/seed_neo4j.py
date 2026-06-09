"""
TransitFlow — Neo4j Seeder
Run once after starting Docker:
    python skeleton/seed_neo4j.py

Loads station and network data from train-mock-data/:
  - metro_stations.json         — city metro stations and adjacencies
  - national_rail_stations.json — national rail stations and adjacencies

This script implements the graph database seeding process.
It strictly adheres to the idempotency principle by using MERGE instead of CREATE,
ensuring that multiple executions will not result in duplicated nodes or relationships.
"""

import json
import os
import sys

sys.path.insert(0, ".")

from neo4j import GraphDatabase
from skeleton.config import NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD

# Define the absolute path to the mock data directory
_DATA_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "train-mock-data")
)


def _load(filename):
    """
    Helper function to load and parse a JSON file from the data directory.
    """
    with open(os.path.join(_DATA_DIR, filename), encoding="utf-8") as f:
        return json.load(f)


def seed():
    """
    Main function to populate the Neo4j graph database.
    It reads JSON mock data and uses Cypher queries to build nodes and relationships.
    """
    # Load station data from JSON files
    metro_stations = _load("metro_stations.json")
    rail_stations  = _load("national_rail_stations.json")

    # Initialize the Neo4j driver using credentials from config.py
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    
    with driver.session() as session:

        # ---------------------------------------------------------
        # 0. Clear existing graph data
        # ---------------------------------------------------------
        # Useful during development to ensure a clean slate before seeding.
        session.run("MATCH (n) DETACH DELETE n")
        print("  Cleared existing graph data")

        # ---------------------------------------------------------
        # 1. Create Metro Station Nodes
        # ---------------------------------------------------------
        # UNWIND is used to process the entire JSON list in a single batch for better performance.
        # MERGE enforces idempotency: it creates the node only if a matching station_id doesn't exist.
        session.run("""
            UNWIND $stations AS s
            MERGE (m:MetroStation {station_id: s.station_id})
            SET m.name = s.name,
                m.lines = s.lines,
                m.is_interchange_nr = s.is_interchange_national_rail,
                m.interchange_nr_id = s.interchange_national_rail_station_id
        """, stations=metro_stations)
        print("  Created MetroStation nodes")

        # ---------------------------------------------------------
        # 2. Create National Rail Station Nodes
        # ---------------------------------------------------------
        # Similar to metro stations, we use a distinct Node Label (:NationalRailStation)
        # to strictly separate the two networks.
        session.run("""
            UNWIND $stations AS s
            MERGE (n:NationalRailStation {station_id: s.station_id})
            SET n.name = s.name,
                n.lines = s.lines,
                n.is_interchange_m = s.is_interchange_metro,
                n.interchange_m_id = s.interchange_metro_station_id
        """, stations=rail_stations)
        print("  Created NationalRailStation nodes")

        # ---------------------------------------------------------
        # 3. Create Metro Network Relationships (:METRO_LINK)
        # ---------------------------------------------------------
        # Iterate through each station's 'adjacent_stations' array.
        # MATCH locates the source and target nodes by their unique IDs.
        # MERGE creates the relationship. SET assigns the travel time and line metadata to the edge.
        session.run("""
            UNWIND $stations AS s
            UNWIND s.adjacent_stations AS adj
            MATCH (a:MetroStation {station_id: s.station_id})
            MATCH (b:MetroStation {station_id: adj.station_id})
            MERGE (a)-[r:METRO_LINK {line: adj.line}]->(b)
            SET r.travel_time_min = adj.travel_time_min
        """, stations=metro_stations)
        print("  Created METRO_LINK relationships")

        # ---------------------------------------------------------
        # 4. Create National Rail Network Relationships (:RAIL_LINK)
        # ---------------------------------------------------------
        session.run("""
            UNWIND $stations AS s
            UNWIND s.adjacent_stations AS adj
            MATCH (a:NationalRailStation {station_id: s.station_id})
            MATCH (b:NationalRailStation {station_id: adj.station_id})
            MERGE (a)-[r:RAIL_LINK {line: adj.line}]->(b)
            SET r.travel_time_min = adj.travel_time_min
        """, stations=rail_stations)
        print("  Created RAIL_LINK relationships")

        # ---------------------------------------------------------
        # 5. Create Cross-Network Interchange Relationships (:INTERCHANGE_WITH)
        # ---------------------------------------------------------
        # Filter stations that act as a bridge between Metro and National Rail.
        # We explicitly create bidirectional relationships (m->n and n->m) 
        # so pathfinding algorithms can easily traverse between networks regardless of the starting point.
        session.run("""
            UNWIND $stations AS s
            WITH s WHERE s.is_interchange_national_rail = true AND s.interchange_national_rail_station_id IS NOT NULL
            MATCH (m:MetroStation {station_id: s.station_id})
            MATCH (n:NationalRailStation {station_id: s.interchange_national_rail_station_id})
            MERGE (m)-[:INTERCHANGE_WITH]->(n)
            MERGE (n)-[:INTERCHANGE_WITH]->(m)
        """, stations=metro_stations)
        print("  Created INTERCHANGE_WITH relationships")

    # Always close the driver connection when done
    driver.close()
    print("\nNeo4j graph seeded successfully.")
    print("   Open http://localhost:7475 to explore the graph.")


if __name__ == "__main__":
    print("Connecting to Neo4j...")
    seed()