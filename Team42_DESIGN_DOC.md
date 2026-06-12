# Database Design Document

## Section 1 — Entity-Relationship Diagram
## Section 2 — Normalisation Justification
### Normalisation Decision
### De-normalisation Trade-off
### Password Hashing Strategy
## Section 3 — Graph Database Design Rationale

**節點 (Nodes)、關係 (Relationships) 與屬性 (Properties) 的設計決策**
在我們的系統架構中，圖形資料被策略性地拆分為三個層次，並各自具備明確的設計理由：

* 
**節點 (Nodes)**：我們將車站儲存為 Nodes，並明確定義了 `MetroStation` 與 `NationalRailStation` 兩種不同的標籤 (Labels) 。這項設計能將捷運與國鐵兩個路網在邏輯上解耦 (Decouple)，避免查詢時不必要地掃描全域系統。


* 
**關係 (Relationships)**：實體的軌道與轉乘通道被建立為關係，包含 `METRO_LINK`、`RAIL_LINK` 以及作為跨網橋樑的 `INTERCHANGE_WITH` 。我們將其儲存為圖形邊緣 (Edges)，是因為它們本質上代表了旅客實體移動的「路徑」，使我們能輕易運用圖形演算法進行導航。


* 
**屬性 (Properties)**：節點僅保留描述性的元資料（如 `station_id`, `name`, `lines`）；而 `travel_time_min`、`fare` 等成本數據則嚴格儲存為「關係的屬性 (Properties on relationships)」 。因為時間與票價代表的是穿越該路徑的邊緣權重 (Edge Weights) ，這是執行最短路徑演算法的必要前提。



**圖形資料庫 (Graph) 與關聯式資料庫 (Relational) 的演算法優勢對比**
對於路徑規劃，Graph Database 在演算法效率上具備壓倒性優勢。在 Neo4j 中，節點關聯透過指標追尋 (**Pointer Chasing**) 原生儲存，這使得系統在計算相鄰車站時具備極低的時間複雜度 。相反地，如果要在 PostgreSQL (Relational Database) 處理未知深度的路線問題，我們必須撰寫極為複雜的遞迴通用資料表運算式 (**Recursive CTEs**) 並不斷累積路徑集合 。這不僅會產生昂貴的資料表關聯 (**JOINs**) 操作，在大規模路網下更會遭遇嚴重的效能瓶頸 。而圖形資料庫則能直接呼叫 APOC 函式庫高效求解。

**核心查詢場景 (Query Types) 與圖形模型應用**
我們的圖形模型完美賦能了以下兩種複雜的查詢類型：

* 
**最短路徑查詢 (Shortest Path)**：為了找尋最快或最便宜的路線，我們在關聯線上賦予了 `travel_time_min` 與 `fare` 屬性 。這使得我們能夠直接呼叫 **Dijkstra** 演算法，以成本作為權重，動態計算出兩點間的絕對最短路徑 。


* 
**延遲漣漪效應分析 (Delay Ripple)**：當車站發生延誤，我們需要找出被波及的周邊車站。透過圖形資料庫專屬的變動路徑深度語法 (Variable-length path syntax, 如 `*1..15`) ，模型允許我們直觀地向外探索，瞬間找出 N 個節點跳數 (**Hops**) 範圍內的影響半徑 。



**節點身份識別 (Node Identity) 設計**
在節點身份的選擇上，我們指定 `station_id` 作為唯一識別節點的屬性 (**Identity Property**) 。原因有二：首先，作為穩定的代理鍵 (Surrogate Key)，它能直接對應到 PostgreSQL 中的 `station_code`，維持雙資料庫架構的一致性 。其次，在資料匯入腳本 (`seed_neo4j.py`) 中，我們使用 `station_id` 作為 **MERGE** 語法的比對條件 ，確保了絕對的冪等性 (**Idempotency**) ，完美防止腳本重複執行時產生無限多個重複的車站節點 。
## Section 4 — Vector / RAG Design
### Embedding & Cosine Similarity
### The RAG Pipeline
### Embedding Dimension 
## Section 5 — AI Tool Usage Evidence

### Example 1: Fixing AI's Oversight on Graph Weights & Error Handling (Error Correction)

* **Context:** 我們的團隊分工處理圖形資料庫：一位組員使用 AI 生成 `queries.py` (Cypher queries)，另一位則用 AI 生成 `seed_neo4j.py` (data ingestion)。在系統整合與 Code Review 時，我們發現了一個嚴重的跨檔案邏輯錯誤 (cross-file logic bug)。AI 生成的 `queries.py` 呼叫了 `apoc.algo.dijkstra` 來計算最短路徑 (shortest-path) 與最便宜路線 (cheapest-route)，但 AI 生成的 `seed_neo4j.py` 卻漏掉為 `INTERCHANGE_WITH` 的關係 (relationships) 賦予 `travel_time_min` 和 `fare` 屬性。這個「權重陷阱 (weight trap)」導致演算法直接崩潰或回傳零。此外，AI 也沒有在資料庫操作中實作 `try...except` 區塊，嚴重違反了我們的團隊合約 (team contract)。
* **Prompt:** *"請為我補全所有的部分，同時我的組員指出有三個問題 隱患 1：APOC Dijkstra 演算法的「權重陷阱」(最嚴重)... 轉乘沒有時間... 沒有票價 (Fare) 屬性... 隱患 2：嚴重違反了團隊合約的 Try-Catch 規定... 隱患 3：多餘的 INTERCHANGE_TO 標籤..."*
* **Outcome:** AI 承認了這個跨檔案整合的盲點並修正了先前的輸出。它更新了 `seed_neo4j.py`，在處理轉乘的 `MERGE` 語句中補上了 `SET r.travel_time_min = 5` 以及預設票價 (`fare`, `fare_first`)。同時，它也重寫了 `queries.py` 中的所有函數，加入了健全的 `try...except` 錯誤處理來防止伺服器崩潰 (server crashes)，並清除了不存在的 `INTERCHANGE_TO` 標籤。這個案例證明了當 AI 處理孤立檔案時，人類進行系統性除錯 (systematic debugging) 的絕對必要性。

### Example 2: Debugging Agent Integration & DTO Implementation via SQL Aliases (Error Correction)

* **Context:** 在本機端驗證完 Database layer 後，我們將其與 LLM Agent 和 Gradio 前端 (frontend) 進行整合。系統在登入後的階段發生崩潰，當 Agent 試圖獲取使用者 Session 詳細資訊時，在終端機拋出了 `KeyError`。我提取了原始的 traceback 來對這個介面合約不匹配 (interface contract mismatch) 的問題進行除錯。
* **Prompt:** *"(Pasted Terminal Error): File "/home/morrislee/transitflow/skeleton/agent.py", line 552, in run_agent user_display = f"{profile['full_name']} (email: {current_user_email}, user_id: {profile['user_id']})" KeyError: 'user_id' (My Architectural Question): 還有這麼多類似問題，為甚麼當初要自己取跟 json 檔中屬性不一樣的名字？"*
* **Outcome:** AI 分析了 traceback 並揪出核心問題：我們安全的雙金鑰資料庫設計 (Dual-Key database design) 與前端 Agent 產生了架構衝突 (architectural conflict)。我們的 Schema 刻意使用明確的名稱如 `user_code` 作為業務金鑰 (business keys)，而前端 Agent 卻硬編碼 (hardcoded) 預期會收到 `user_id` 這種通用欄位。為了不破壞我們嚴謹的 Database schema 來迎合弱型別的前端，AI 建議使用 SQL 別名 (SQL aliases, 例如 `SELECT user_code AS user_id`) 來實作資料傳輸物件 (Data Transfer Object, DTO) 模式。這個優雅的解法立刻消除了前端的崩潰，同時完美保留了我們資料庫的結構完整性 (structural integrity)。

### Example 3: Identifying AI-Generated Syntax Errors in Neo4j Queries (Error Correction)

* **Context:** 我的組員使用 AI 工具來生成 Neo4j 的路徑查詢 (`databases/graph/queries.py`)。在測試跨網絡路徑 (cross-network paths) 時，資料庫雖然成功找到了正確的車站，卻總是回傳空的 `interchange_points` 陣列，這進一步導致 UI Agent 產生幻覺 (hallucinate)，向使用者虛構出不存在的轉乘路線。我強烈懷疑最初 AI 生成的查詢語法有缺陷，因此擷取了該片段並要求 AI 重新檢視 (review)。
* **Prompt:** *"[附上包含 `type(r) == 'INTERCHANGE_WITH'` 的 query_interchange_path 程式碼片段] 組員寫的真的有bug嗎"*
* **Outcome:** AI 深度分析了該片段，並承認它（或另一個 LLM）最初生成的程式碼包含了一個經典的 Neo4j Python driver 陷阱 (pitfall)。它錯誤地使用了 Python 內建的 `type(r)` 函數，這會回傳一個物件類別 `<class 'neo4j.graph.Relationship'>`，導致字串比較 `== 'INTERCHANGE_WITH'` 默默失敗 (fail silently) 卻不報錯。AI 修正了這個語法錯誤，指示我必須使用 Neo4j 的資料庫屬性 `r.type` 來代替。我將這個修正應用到所有四個 Graph routing functions 中，立刻解決了空陣列的 Bug，並成功阻止了 UI Agent 發生下游的幻覺問題 (downstream hallucinations)。

---

## Section 6 — Reflection & Trade-offs
### Design Decisions
### Production Considerations
## Section 7 — Task 6 Extension (Optional)
### Motivation
### Database Changes
### Example Queries
### Testing Evidence