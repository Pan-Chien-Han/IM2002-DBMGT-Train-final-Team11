// Deprecated: seeding is now done via skeleton/seed_neo4j.py
// which loads data directly from train-mock-data/ JSON files.
//
// If you prefer Cypher-file seeding, implement your graph schema here.
// Run with: python skeleton/seed_neo4j.py (or via the Neo4j Browser)

// --- 在下方加入你的 Schema 定義 ---

// 1. 確保捷運車站 ID 是唯一的，建立索引以加快 MATCH 速度
CREATE CONSTRAINT FOR (s:MetroStation) REQUIRE s.station_id IS UNIQUE;

// 2. 確保國家鐵路車站 ID 是唯一的（為之後匯入 national_rail 做準備）
CREATE CONSTRAINT FOR (s:NationalRailStation) REQUIRE s.station_id IS UNIQUE;