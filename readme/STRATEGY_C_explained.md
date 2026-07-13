# Strategy C อธิบายใหม่ — แยกทีละภาพ ไม่ยัดรวม

> อัปเดต: 13 Jul 2026 — รวม parser fix + journal_page + **per-source + LLM rerank**  
> ไฟล์เดิม (`STRATEGY_C.md`) ยัดทุกอย่างไว้ใน diagram เดียวตอนท้าย ทำให้งง  
> ไฟล์นี้แยกเป็น **6 ภาพเล็กๆ ทีละเรื่อง** อ่านทีละภาพจบในตัว

---

## ภาพรวมที่สุด (จำแค่นี้พอ)

```mermaid
flowchart LR
    A["เอกสาร .md"] --> B["1. ตัดเป็น chunk"]
    B --> C["2. เก็บใน Qdrant"]
    C --> D["3. ตอนถาม: ค้นหา + กรอง"]
    D --> E["ส่งให้ AI ตอบ"]

    classDef step fill:#e7f5ff,stroke:#1864ab,stroke-width:2px,color:#1864ab
    class A,B,C,D,E step
```

**5 เรื่องเดิม + เรื่องใหม่:**
1. ตัดตามหัวข้อ
2. แยกตาราง
3. ตัดย่อหน้ายาว (overlap)
4. กรอง patient_group ตอนค้นหา
5. Parser fix + เลขหน้า 2 แบบ
6. **ดึงแยกเล่ม + LLM rerank** (13 Jul 2026)

> **Production ใช้ code นี้แล้ว** — `backend/md_chunker.py`, `embed_to_qdrant.py`, `rag_engine.py`  
> รัน `python pipeline.py --reset` → **132 chunks** (AAFP 38 + URI 94)  
> Retrieve: **filter → per-source → LLM rerank (fallback BM25) → top_k**

---

## เรื่องที่ 1: ตัดตามหัวข้อ (ไม่ปนหัวข้อ)

### ปัญหาของวิธีเดิม

```mermaid
flowchart TB
    DOC["เอกสาร:<br/>Common Cold ... ตาราง ... Strep Throat"]
    DOC --> OLD["วิธีเดิม: ยัดทุกอย่างต่อกันเป็นก้อนเดียว<br/>พอยาวเกิน 500 tokens ค่อยตัด"]
    OLD --> BAD["chunk หนึ่งมีทั้ง<br/>'หวัด' ปนกับ 'เจ็บคอ'"]

    classDef neutral fill:#e7f5ff,stroke:#1864ab,stroke-width:2px,color:#1864ab
    classDef bad fill:#ffe3e3,stroke:#c92a2a,stroke-width:2px,color:#c92a2a
    class DOC,OLD neutral
    class BAD bad
```

### วิธีใหม่: พอเจอหัวข้อใหม่ ตัด chunk ทันที

```mermaid
flowchart TB
    L1["อ่าน: '# Antibiotic Use'"] --> BOX1["เปิดกล่องใหม่ ชื่อ 'Antibiotic Use'"]
    BOX1 --> L2["อ่าน: ย่อหน้า intro"] --> ADD1["ใส่ในกล่อง"]
    ADD1 --> L3["อ่าน: '### Common Cold' ← หัวข้อใหม่!"]
    L3 --> CLOSE["ปิดกล่องเดิม → สร้าง chunk จากของในกล่อง"]
    CLOSE --> BOX2["เปิดกล่องใหม่ ชื่อ 'Common Cold'"]

    classDef action fill:#e7f5ff,stroke:#1864ab,stroke-width:2px,color:#1864ab
    classDef trigger fill:#fff3bf,stroke:#e67700,stroke-width:2px,color:#e67700
    classDef result fill:#ebfbee,stroke:#2b8a3e,stroke-width:2px,color:#2b8a3e

    class L1,L2,BOX1,ADD1 action
    class L3 trigger
    class CLOSE,BOX2 result
```

**ผลลัพธ์:**

| chunk | heading | เนื้อหา |
|---|---|---|
| AAFP_0000 | Antibiotic Use | เฉพาะ intro (ไม่ปน Common Cold) |
| AAFP_0002 | Antibiotic Use > Common Cold | เฉพาะย่อหน้าเรื่องหวัด |

---

## เรื่องที่ 2: ตารางแยกเป็น chunk ของตัวเองเสมอ

### เทียบเดิม vs ใหม่

```mermaid
flowchart TB
    subgraph OLD["เดิม — ตารางติดกับข้อความ"]
        direction TB
        O1["ย่อหน้าก่อนตาราง"] --> O2["ตาราง 8 โรค"] --> O3["ย่อหน้าหลังตาราง"]
        O1 --- OC["รวมเป็น chunk เดียวกันหมด<br/>ถ้ายาวเกิน → ตารางขาดกลาง"]
        O2 --- OC
        O3 --- OC
    end

    subgraph NEW["ใหม่ — ตารางแยกทันที"]
        direction TB
        N1["ย่อหน้าก่อนตาราง"] --> C1["chunk A0000<br/>(เฉพาะย่อหน้า)"]
        N2["ตาราง 8 โรค"] --> C2["chunk A0003<br/>type: table_html<br/>(เฉพาะตาราง ครบทุกแถว)"]
        N3["ย่อหน้าหลังตาราง"] --> C3["chunk A0004<br/>(เฉพาะย่อหน้า)"]
    end

    classDef bad fill:#ffe3e3,stroke:#c92a2a,stroke-width:2px,color:#c92a2a
    classDef good fill:#ebfbee,stroke:#2b8a3e,stroke-width:2px,color:#2b8a3e
    class OC bad
    class C1,C2,C3 good
```

**ทำไมสำคัญ:** ถ้าเภสัชกรถาม "ตารางมีโรคอะไรบ้าง" → ระบบดึง `chunk A0003` มาตรงๆ ได้ครบทุกแถว

---

## เรื่องที่ 3: ย่อหน้ายาวเกิน ตัดยังไงไม่ให้ประโยคขาด

```mermaid
flowchart LR
    P1["ย่อหน้า 1"] --> P2["ย่อหน้า 2"] --> P3["ย่อหน้า 3"] --> P4["ย่อหน้า 4"] --> P5["ย่อหน้า 5"]

    P1 -.รวมกัน ยังไม่เกิน 500 tok.-> CHUNK1["Chunk 1 = ย่อหน้า 1+2+3"]
    P3 -.เกินแล้ว! เอาท้าย 80 tok ของ chunk 1.-> OVERLAP["ท้ายของ chunk 1<br/>(กันประโยคขาด)"]
    OVERLAP --> CHUNK2["Chunk 2 = [ท้าย chunk1] + ย่อหน้า 4+5"]

    classDef normal fill:#e7f5ff,stroke:#1864ab,stroke-width:2px,color:#1864ab
    classDef overlap fill:#fff3bf,stroke:#e67700,stroke-width:2px,color:#e67700
    classDef chunk fill:#ebfbee,stroke:#2b8a3e,stroke-width:2px,color:#2b8a3e

    class P1,P2,P3,P4,P5 normal
    class OVERLAP overlap
    class CHUNK1,CHUNK2 chunk
```

**สรุปสั้น:** ตัดที่ขอบย่อหน้าเท่านั้น + overlap **80 tokens** ระหว่าง chunk

---

## เรื่องที่ 4: ตอนถาม ระบบกรองยังไง (ป้าย patient_group)

### Step by step ของคำถามเคสเด็ก

```mermaid
flowchart TB
    Q["คำถาม: 'เด็กอายุ 3 ขวบ น้ำมูกเขียว ควรจ่ายยาอะไร'"]
    Q --> READ["อ่านคำถาม → เจอคำว่า '3 ขวบ' → เคสนี้คือเด็ก (pediatric)"]
    READ --> DECIDE["ตัดสินใจว่าจะเอา chunk ป้ายไหนบ้าง"]

    DECIDE --> KEEP["เอา:<br/>pediatric (ตรงเคส)<br/>both (มีทั้งเด็ก+ผู้ใหญ่)<br/>general (ข้อมูลทั่วไป)"]
    DECIDE --> DROP["ไม่เอา:<br/>adult (เช่น Centor score ผู้ใหญ่)"]

    KEEP --> SEARCH["ค้นหาเฉพาะ chunk กลุ่มที่เก็บไว้ → เอา 5 อันดับแรก"]
    SEARCH --> RESULT["ส่งให้ AI ตอบ"]

    classDef q fill:#e7f5ff,stroke:#1864ab,stroke-width:2px,color:#1864ab
    classDef keep fill:#ebfbee,stroke:#2b8a3e,stroke-width:2px,color:#2b8a3e
    classDef drop fill:#ffe3e3,stroke:#c92a2a,stroke-width:2px,color:#c92a2a

    class Q,READ,DECIDE,SEARCH,RESULT q
    class KEEP keep
    class DROP drop
```

**ทำไมไม่กรองเอาแค่ `pediatric` อย่างเดียว?**  
PDF ไม่ได้แยก section เด็ก/ผู้ใหญ่ชัดเจน — ใช้แบบ **inclusive**: เอาที่ตรงเคส + ทั่วไป + both แต่ตัดกลุ่มตรงข้ามออก

**Production (อัปเดต 13 Jul):** หลัง filter แล้ว ยังมี **ดึงแยกเล่ม + rerank** — ดูเรื่องที่ 6

---

## เรื่องที่ 5: Parser fix + เลขหน้า 2 แบบ (ใหม่ Jul 2026)

### ปัญหาที่พบ — ไม่ใช่ OCR

```mermaid
flowchart TB
    MD["AAFP.md มี 7 ตาราง + PAGE marker ถูกต้อง"]
    MD --> BUG["บั๊ก parser เก่า:<br/>ไม่เช็ค &lt;/table&gt; บรรทัดแรก<br/>→ กลืนบรรทัดจนกว่าจะเจอตารางถัดไป"]
    BUG --> LOST["PAGE marker หาย<br/>7 ตาราง → 4 mega-block<br/>chunk ผิดหน้า"]

    classDef bad fill:#ffe3e3,stroke:#c92a2a,stroke-width:2px,color:#c92a2a
    class MD,BUG,LOST bad
```

### แก้ยังไง (อยู่ใน production แล้ว)

```mermaid
flowchart LR
    FIX1["เช็ค &lt;/table&gt; บรรทัดเปิดตาราง"] --> FIX2["แยก text ก่อน &lt;table&gt; บรรทัดเดียวกัน"]
    FIX2 --> OK["7/7 ตาราง AAFP<br/>PAGE marker ครบ"]

    classDef good fill:#ebfbee,stroke:#2b8a3e,stroke-width:2px,color:#2b8a3e
    class FIX1,FIX2,OK good
```

### เลขหน้า 2 แบบ — อย่าสับสน

| field | ความหมาย | ใช้ที่ไหน | ตัวอย่าง |
|---|---|---|---|
| `page` | เลขหน้า PDF (จาก `<!-- PAGE N -->`) | เว็บ `#page=N`, [Ref] ปัจจุบัน | PDF หน้า 2 |
| `journal_page` | เลขหน้าวารสาร (American Family Physician) | eval test case P.628–636 | 628, 629, 632 |

**ทำไม Run 1 Page Recall ได้แค่ 12%?**  
วัดผิด — เทียบ `page` (PDF) กับ `expected_pages` (เลขวารสาร)  
Run 2 ใช้ `Page Recall@5 (journal)` เป็น metric หลัก

**Map ตัวอย่าง AAFP:**

| PDF `page` | `journal_page` |
|---|---|
| 1 | 628 |
| 2 | 629 |
| 4 | 631 |
| 5 | 632 |
| 6 | 633 |
| 8 | 635 |

---

## เรื่องที่ 6: ดึงแยกเล่ม + LLM rerank (13 Jul 2026)

### ปัญหา — คาด AAFP แต่ได้ URI ทั้งก้อน

```mermaid
flowchart TB
    Q["คำถามไทย: เด็ก 3 ขวบ หวัด"] --> OLD["ค้นรวมกองเดียว top-5"]
    OLD --> BAD["URI ชนะทุกอันดับ<br/>(ภาษาไทย + 94 chunks)<br/>AAFP หลุดแม้เกี่ยวข้อง"]

    classDef bad fill:#ffe3e3,stroke:#c92a2a,stroke-width:2px,color:#c92a2a
    class Q,OLD,BAD bad
```

### วิธีใหม่ใน production (`rag_engine.search_chunks`)

```mermaid
flowchart TB
    Q2["คำถาม"] --> F["1. กรอง patient_group"]
    F --> S1["2a. ดึง top จาก AAFP"]
    F --> S2["2b. ดึง top จาก URI"]
    S1 --> POOL["รวม candidate pool"]
    S2 --> POOL
    POOL --> RR["3. LLM rerank<br/>(Gemini จัดอันดับ id)"]
    RR --> COV["4. Source coverage<br/>อย่างน้อย 1 ต่อเล่มถ้ามี"]
    COV --> OUT["top_k ส่งให้ LLM ตอบ"]

    classDef good fill:#ebfbee,stroke:#2b8a3e,stroke-width:2px,color:#2b8a3e
    class Q2,F,S1,S2,POOL,RR,COV,OUT good
```

**ทำไมใช้ LLM แทน BM25 เป็น default?**  
BM25 บน pool เล็กๆ ไม่ได้นาน — แต่ LLM เข้าใจบริบทโรค/กลุ่มผู้ป่วยดีกว่า (เช่น AAFP Choosing Wisely vs URI หวัดทั่วไป)  
ถ้า LLM ล้มเหลว/quota → **fallback BM25 อัตโนมัติ**

| ค่า | ความหมาย |
|---|---|
| `RERANK_MODE=llm` | default — Gemini จัดอันดับ |
| `RERANK_MODE=bm25` | hybrid vector+BM25 (ไม่เรียก LLM เพิ่ม) |
| `RERANK_MODE=vector` | เรียงตาม cosine อย่างเดียว |
| `CANDIDATE_MIN_SCORE=0.55` | เกณฑ์ตอนดึงต่อเล่ม |
| `PER_SOURCE_TOP_K=8` | ดึงสูงสุด 8 ต่อเล่มก่อนรวม (~16 candidates ก่อน rerank) |

**ตัวอย่างหลังแก้:**
- เด็กหวัด → top-5 มี **URI + AAFP** (ไม่ใช่ URI ล้วน)
- ผู้ใหญ่ไซนัส → **AAFP** (URI ถูก filter pediatric ตัดออกอยู่แล้ว)

---

## สรุปตัวเลข (experiment — **25 cases** AAFP+URI)

| | Run 1 | Run 2 (vector+filter) | **Run 3 (per-source + LLM)** |
|---|---|---|---|
| Source Recall@5 | 84% | 80% | **100%** |
| MRR | 0.55 | 0.63 | **0.70** |
| Page Recall (journal) | — | 56% | **64%** |
| Page Recall (pdf) | 12% | 12% | 12% |
| Group Accuracy | 100% | 100% | **100%** |
| Chunks | 72 | 132 | 132 |

**ชุดทดสอบ:** `test_case.csv` มี **57** แถว — eval ใช้ **25** (AAFP 22 + URI 3)

**สิ่งที่ยังต้องแก้ต่อ:**
- Frontend ยังแสดง `page` (PDF) ไม่ใช่ `journal_page` ใน [Ref]
- Dose supportive layer ยังไม่ merge
- Safety gate เด็ก <4 ปี ยังไม่มี
- ยังไม่วัดคุณภาพคำตอบ LLM end-to-end

---

## เทียบให้เห็นภาพเดียวจบ

```mermaid
flowchart TB
    subgraph OLD["Strategy A — เดิม"]
        direction TB
        A1["ยัดทุกย่อหน้า + ตาราง รวมกัน"] --> A2["parser กลืนตาราง<br/>PAGE marker หาย"]
        A2 --> A3["ค้นหา: ไม่มีป้ายกรอง"]
    end

    subgraph NEW["Strategy C + Parser Fix + Per-source — production"]
        direction TB
        C1["แยกกล่องตามหัวข้อ + แยกตาราง 7/7"] --> C2["overlap 80 tok + journal_page"]
        C2 --> C3["ติดป้าย patient_group"]
        C3 --> C4["ค้นหา: filter → แยกเล่ม<br/>→ LLM rerank"]
    end

    classDef bad fill:#ffe3e3,stroke:#c92a2a,stroke-width:2px,color:#c92a2a
    classDef good fill:#ebfbee,stroke:#2b8a3e,stroke-width:2px,color:#2b8a3e

    class A1,A2,A3 bad
    class C1,C2,C3,C4 good
```

---

## ไฟล์ที่เกี่ยวข้อง

| ไฟล์ | ทำอะไร |
|---|---|
| `backend/md_chunker.py` | Strategy C + parser fix + journal_page |
| `backend/embed_to_qdrant.py` | embed + เก็บ payload |
| `backend/rag_engine.py` | filter + per-source + LLM rerank (fallback BM25) |
| `pipeline.py` | CLI chunk + embed (`--reset`) |
| `data/chunks.jsonl` | 132 chunks ปัจจุบัน |
| `experiments/chunking/` | notebook วัดผล A/B/C |
