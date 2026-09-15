# Rà soát tuân thủ Spec.md v1.4 — FR-01 đến FR-13

Ngày rà soát: 2026-09-15  
Mốc mã được dùng khi lập kế hoạch: c6f703f2c7a06d1430aa6b3258c7fbeeef53e8d9  
Phạm vi: functional requirements FR-01–FR-13, ranh giới kiến trúc, vòng đời phiên, ingestion, bảo mật dữ liệu, thống kê, Verification Gates, UI, artifact và export.

## 1. Phương pháp và mức độ tin cậy của bằng chứng

- Đã đọc toàn bộ Spec.md v1.4; các amendment ở cuối tài liệu được ưu tiên hơn mô tả cũ. Đã đọc CONTEXT.md, docs/architecture.md, docs/limitations.md, docs/mvp-acceptance.md, các milestone liên quan và docs/review-plan.md.
- Đã đọc skill codebase-memory tại C:/Users/A Fee/.agents/skills/codebase-memory/SKILL.md và dùng knowledge graph project data_analyst_agent để định vị module trước khi đọc source hiện tại. Graph chỉ có 29 Python files, trong khi source hiện tại có nhiều file hơn, nên mọi kết luận dưới đây dựa trên source hiện tại và graph chỉ là chỉ dẫn.
- Không gọi live model/API, không đọc giá trị secret, không xóa dữ liệu, không sửa source/fixture/holdout. docs/RESUME-HANDOFF.md được giữ nguyên.
- docs/mvp-acceptance.md:3-4 ghi checklist theo v1.1; các số liệu và amendment v1.4 ở phần sau được xem là bằng chứng lịch sử, không tự động được coi là nghiệm thu release hiện tại. Bản thân checklist ghi rõ unit test, live evaluation và manual acceptance là ba tuyên bố khác nhau.

Nhãn dùng trong báo cáo:

- **Đạt (code):** đường đi trong source đáp ứng hợp đồng ở mức tĩnh; chưa có nghĩa là đã manual/live accepted.
- **Một phần:** đã có triển khai nhưng còn lỗi, giới hạn hoặc đường đi chưa hoàn chỉnh.
- **Khoảng trống:** hợp đồng Must chưa được triển khai hoặc boundary không cho phép trường hợp mà Spec yêu cầu.
- **Chưa kiểm chứng:** không đủ điều kiện hoặc chưa chạy phép kiểm tương ứng; không suy diễn từ unit test.
- **Được chấp thuận:** giới hạn/feature thuộc Should hoặc Could, đã được Spec hoặc tài liệu chấp thuận hoãn.

## 2. Kết luận điều hành

**Ghi chú kiểm tra chéo của điều phối:** ưu tiên cuối cùng theo `review-summary.md`. F6 là khoảng trống làm rõ hợp đồng, chưa phải lỗ hổng bảo mật xác nhận: CSV không có magic bytes phổ quát, MIME từ browser không đáng tin và không nên reject CSV hợp lệ chỉ do MIME. Với F3, truyền annotations đáng tin cậy vào renderer là đủ cho bugfix; hợp nhất/migrate fingerprint là việc riêng. Với F1, ưu tiên ngoại lệ dựa trên AST và dataset identity hiện có; chỉ thêm marker/schema mới nếu cần để giữ invariant, tránh mở rộng contract không cần thiết.

Các lớp typed contract, LangGraph state machine/checkpoint, SQL AST policy, deterministic statistics/verification, evidence budget, artifact store và ModelGateway là complexity có căn cứ trực tiếp trong Spec.md; chưa có lý do để big-bang rewrite hay xóa chúng. docs/review-overfit-architecture.md phân tích riêng phần overfit/overengineering.

MVP hiện có nền tảng tốt cho ingestion, profile, planning, read-only execution, statistics, verified claims, charts, sessions và CSV/JSON. Tuy nhiên chưa thể ghi nhận toàn bộ Must là **Đạt** vì bốn đường đi có bằng chứng cụ thể:

1. Filter nằm trong CTE/subquery bị mất khỏi QueryResult.filters, nên Evidence Trail của một claim có thể không nêu phạm vi lọc.
2. Spec cho phép plan COUNT(*) toàn bảng không có identifier/source field, nhưng planner và verification bắt buộc SQL step phải có field.
3. Chart sau khi user xác nhận Semantic Annotation bị renderer từ chối vì hai bên tính semantic fingerprint khác nhau.
4. KPI mặc định dùng định dạng tối đa 12 chữ số có nghĩa, đồng thời QueryResult downcast Decimal sang float, trái với yêu cầu hiển thị mọi chữ số có nghĩa.

Hai điểm chất lượng bổ sung cần xử lý trước khi gọi release-ready: Mann–Whitney/Kruskal–Wallis trả assumptions=() dù FR-09 yêu cầu kiểm tra assumption áp dụng; nút “Dismiss this question” chỉ ẩn state trên UI và không ghi trạng thái hủy vào checkpoint. CSV cũng chưa có MIME/content-signature contract rõ ràng.

## 3. Ma trận FR-01–FR-13

| FR | Tier | Kết quả hiện tại | Bằng chứng source/docs | Khoảng trống hoặc giới hạn cần theo dõi |
|---|---|---|---|---|
| FR-01 Session management | Must | **Một phần** | application/service.py:116-175,178-253,255-279,281-335; streamlit_app.py:154-223 | Create/list/open/resume/status/delete, namespace, UUID path và confirmation theo session đã có. Delete artifact rồi filesystem tuần tự đúng amendment. Nút dismiss semantic không cập nhật durable state (F7, P2); manual UI reopen/restart chưa kiểm chứng. |
| FR-02 File ingestion | Must | **Một phần** | application/service.py:116-142; data/core.py:103-141,143-198,324-354,626-655; streamlit_app.py:917-947 | CSV kiểm tra extension, size, encoding, delimiter, NUL/header và parse; XLSX kiểm tra ZIP signature, archive path/ratio/size, workbook/sheet và đọc data_only=True. CSV chưa mang MIME/content-signature vào boundary (F6, P2). |
| FR-03 Data profiling | Must | **Đạt có giới hạn** | data/core.py:200-234,479-584,689-754; domain/models.py:165-189 | Có shape, type, missing, unique, duplicate, robust numeric summary, category, date, constant/near-constant/id/high-cardinality, spelling, outlier heuristic, PII. Heuristic PII tiếng Anh và ngưỡng id tối thiểu 20 được ghi nhận ở docs/limitations.md:51-58; đây là limitation đã công khai, không phải lỗi mới. |
| FR-04 Semantic clarification | Must | **Một phần** | orchestration/graph.py:249-361,363-461; orchestration/binding.py:67-147; streamlit_app.py:483-545 | Có mapping direct/derived/unavailable, annotation xác nhận, corrected request re-interpret và typed refusal. Chất lượng model với ranking mơ hồ còn giới hạn đã ghi trong docs/limitations.md:125-128; dismiss bền vững là F7. |
| FR-05 Analytical goals | Must | **Đạt về code; live quality chưa kiểm chứng** | application/suggestions.py:11-57; orchestration/graph.py:249-361; streamlit_app.py:742-747 | 3–5 suggestion deterministic, không model call, loại PII/id; các goal family và Vietnamese request được prompt/contract hỗ trợ. Chưa chạy manual/live evaluation trong đợt này. |
| FR-06 Analysis planning | Must | **Một phần** | orchestration/graph.py:463-577,579-610; orchestration/binding.py:55-65; streamlit_app.py:441-561 | Plan có steps, fields, expected output, caveat, budget và approval; significance bắt buộc statistical step. Contract COUNT(*) không field bị chặn (F1, P1). |
| FR-07 Working Dataset transformations | Should | **Chưa triển khai; được chấp thuận hoãn** | data/core.py:143-198 giữ source/hash; docs/limitations.md:60-74; docs/mvp-acceptance.md:69-71 | Source immutable đã có nhưng chưa có transform/approval/evidence trail cho cleaning. Đây không phải Must blocker; cần ghi rõ trên UI khi user yêu cầu transform. |
| FR-08 Analytical execution | Must | **Đạt nền tảng; một phần về field-id edge case** | data/core.py:236-322; data/sql_policy.py:79-223; statistics/service.py:59-195; statistics/engine.py:43-81 | SQL/statistics typed, deterministic và không dùng LLM arithmetic. Qualified ASCII id như d.c5 không rewrite ở data/sql_policy.py:44-69, đã gặp ở holdout v9 và được phân tích chi tiết trong review overfit; cần xử lý hoặc reject rõ trước release. |
| FR-09 Statistical analysis | Must | **Một phần** | statistics/models.py:27-235; statistics/engine.py:43-81,138-388; statistics/service.py:59-195 | Tất cả operation trong danh sách và effect/multiple-testing/significance/missing/sample được trả về. Mann–Whitney/Kruskal–Wallis để trống assumptions (F5, P2); assumptions phải được kiểm hoặc ghi NOT_CHECKED có lý do. |
| FR-10 Verified Insights | Must | **Một phần** | verification/query_evidence.py:14-67; verification/insights.py:95-238,278-434; domain/models.py:331-377; orchestration/evidence_catalog.py:51-267 | Assertion/evidence/action/version/claim-scope deterministic đã có. F2 làm mất filter scope trong CTE; F1 làm COUNT(*) không thể thành evidence. verification/insights.py:281-284 lấy source fields từ required_fields đã duyệt thay vì actual referenced columns; nên giữ rõ đây là approved-read scope hoặc đổi tên trường để tránh hiểu là exact-read scope. |
| FR-11 Charts and dashboard | Must + Should | **Một phần** | visualization/renderer.py:27-54,70-228,231-427; visualization/artifacts.py:63-104,171-200,373-401; application/overview.py:62-126,439-586; streamlit_app.py:381-430,573-701,787-801 | Must chart renderer/candidate/pinned/Data Overview/explorer đã có. F3 semantic fingerprint làm chart sau annotation fail; F4 KPI precision. Stacked bar/HTML là Should còn thiếu; artifact current filter đã kiểm tra dataset/version/fingerprint, không có bằng chứng stale bug ở _list_current. |
| FR-12 Conversational follow-up | Must | **Một phần** | application/service.py:281-335; orchestration/graph.py:158-206,363-461,579-610; verification/insights.py:254-275; visualization/artifacts.py:373-401 | New request dùng session/profile và confirmed annotations; stale insight/artifact bị loại theo version/fingerprint. UI dismiss chưa cancel durable checkpoint (F7). Transform follow-up chưa có vì FR-07 Should. |
| FR-13 Export | Must CSV/JSON, Should HTML | **CSV/JSON Đạt về code; HTML được chấp thuận hoãn** | application/exports.py:37-124,132-165,168-196; streamlit_app.py:633-675; docs/mvp-acceptance.md:30-32,69-71 | CSV có formula neutralization + UTF-8 BOM; JSON loại rows/prompts/traces/provider và giữ metadata/stat results. Current session/dataset/version/fingerprint được kiểm. HTML chưa có, đúng Should. Chưa manual-accept toàn bộ download flow. |

## 4. Phát hiện cần hành động

### F1 — P1/Must: COUNT(*) không có source field bị chặn ở nhiều boundary

**Điều khoản:** Spec.md:163-166, Spec.md:514, Spec.md:768, Spec.md:789, Verification Gates 1/4/10 ở Spec.md:439-448. Amendment 25.4 nói rõ counting rows/records không phải requested metric, không cần mapping hay identifier, và plan dùng COUNT(*) không có field.

**Bằng chứng:**

- orchestration/binding.py:55-65 phát hiện mọi SQL PlanStep có required_fields=() và buộc re-plan.
- verification/query_evidence.py:43-50 chỉ pass schema_grounding khi bool(source_fields).
- verification/insights.py:168-178,203-220 cũng yêu cầu source field và chỉ tạo EvidenceTrail khi có field.
- domain/models.py:346-358 từ chối Evidence Trail rỗng source_fields.
- tests/test_orchestration.py:1123-1136 xác nhận hành vi hiện tại: SQL plan step không field bị đưa vào repair.

**Tình huống:** Câu hỏi “đếm số record/order/reading của dataset” có thể được trả trực tiếp từ profile khi đúng là whole-dataset profile fact (Spec.md:83). Vì vậy không nói rằng mọi row count hiện đều thất bại. Khoảng trống nằm ở SQL planning contract: khi planner chọn SELECT COUNT(*) AS row_count FROM dataset theo 25.4, step không có requested metric mapping/identifier là trạng thái hợp lệ theo Spec nhưng bị repair; nếu vượt qua planner thì verification/evidence vẫn fail.

**Sửa tối thiểu:** thêm một typed scope/output marker cho whole-dataset row count. Chỉ cho phép required_fields=() khi AST chứng minh truy vấn là COUNT(*) hợp lệ và không đọc field; count có filter/group vẫn phải khai báo field dùng cho filter/group. Verification và EvidenceTrail cho phép empty source fields chỉ với marker này và ghi count_scope=whole_dataset, không mở rộng ngoại lệ cho COUNT(id), literal hoặc query khác.

**Test hồi quy:** empty-field COUNT(*) được plan, execute, verify, publish và export; COUNT(id) không được hưởng ngoại lệ; filtered/grouped count yêu cầu đúng field; profile-only row count không tạo Tool Action; result/action/dataset/version vẫn phải pass.

### F2 — P1/Must: filter trong CTE/subquery không đi vào Evidence Trail

**Điều khoản:** Spec.md:225 yêu cầu claim filtered query nêu điều kiện SQL WHERE/HAVING và lưu chúng trong Evidence Trail; Gate 3 ở Spec.md:441 yêu cầu filter/group/aggregation được biểu diễn; Gate 10 ở Spec.md:448 yêu cầu trail đầy đủ.

**Bằng chứng:** data/sql_policy.py:215-223 chỉ đọc statement.args[where] và statement.args[having] của outer exp.Select. data/core.py:236-254,310-322 đưa inspection.filters vào QueryResult; verification/insights.py:287-293 ưu tiên result.filters rồi mới fallback sang action input. Không có bước thu thập WHERE/HAVING trong CTE/subquery.

**Tái hiện offline trên source hiện tại:**

~~~text
direct: SELECT region, COUNT(*) AS n FROM dataset
        WHERE amount > 10 GROUP BY region
filters = ('amount > 10',)

equivalent CTE: WITH filtered AS (
        SELECT region, amount FROM dataset WHERE amount > 10
    ) SELECT region, COUNT(*) AS n FROM filtered GROUP BY region
filters = ()
~~~

**Tác động:** query vẫn có thể tính đúng, nhưng claim có thể nói tổng theo nhóm mà không cho biết đã lọc amount > 10. Đây là mất provenance, không phải stale artifact bug; artifact store hiện lọc current dataset/version/fingerprint ở visualization/artifacts.py:373-401.

**Sửa tối thiểu:** traversal AST theo scope để thu thập mọi điều kiện lọc thực sự giới hạn nguồn dataset, giữ thứ tự/canonical SQL và phân biệt scope CTE/outer nếu cần. Không chỉ nối chuỗi lỗi model. Có thể mở rộng QueryInspection.filters thành record có scope nếu chuỗi hiện tại không đủ mô tả.

**Test hồi quy:** direct WHERE, outer HAVING, WHERE trong CTE, WHERE trong subquery, nhiều CTE và query không filter; sau publish kiểm tra VerifiedInsight.evidence.filters đúng với SQL và export giữ nguyên.

### F3 — P1/Must: Semantic Annotation fingerprint làm Chart Intent bị từ chối

**Điều khoản:** Spec.md:232, Spec.md:235-236, Spec.md:256, Spec.md:445-448; chart phải tham chiếu exact verified result và annotation change phải làm result/artifact stale.

**Bằng chứng:** orchestration/graph.py:1103-1125 tạo ChartIntent.source_result_ref bằng make_query_result_reference(result, annotations). Nhưng visualization/renderer.py:70-99 trong validate_chart_intent tạo expected_ref = make_query_result_reference(result) với annotation tuple rỗng.

**Tái hiện offline:** dùng fixture tests/test_visualization.py:29-92. Với cùng QueryResult/ToolAction, không annotation thì source_result_binding pass. Thêm annotation revenue => Net revenue thì check trả passed=False, thông báo Chart Intent or Tool Action does not reference this Query Result. Fingerprint trong intent và fingerprint rỗng của renderer khác nhau. Đây là reproduction độc lập, không gọi provider.

**Tác động:** semantic review có thể hoàn thành và insight được verified, nhưng bước propose artifact để lại chart_renders=[] và artifact_error ở orchestration/graph.py:1125-1146; user mất Must-tier chart sau khi xác nhận nghĩa field.

**Sửa tối thiểu:** truyền tuple annotation hiện tại qua validate_chart_intent/render_chart, hoặc tạo expected reference từ context semantic đã được kiểm tra ở một boundary duy nhất. Vẫn phải giữ exact query_id, dataset/version, action output ref và stale rejection; không tắt fingerprint check.

**Test hồi quy:** semantic confirm → query → verified insight → bar/table render; đổi annotation làm artifact stale; khác query/dataset/version hoặc unverified action vẫn bị reject; no-cell-values/no-aggregation gates vẫn pass.

### F4 — P1/Must: KPI không bảo toàn mọi chữ số có nghĩa

**Điều khoản:** Spec.md:232 yêu cầu KPI hiển thị đúng một giá trị từ one-row result với mọi significant digit.

**Bằng chứng:** visualization/renderer.py:332-341 dùng mặc định number={"valueformat": ",.12~g"}; comment cùng đoạn thừa nhận định dạng Plotly mặc định làm tròn và implementation chỉ nâng giới hạn lên 12 chữ số có nghĩa. Trước đó data/core.py:801-813 chuyển cả Decimal sang float ở dòng 808-810.

**Tình huống:** one-row SUM/revenue có hơn 12 chữ số có nghĩa hoặc giá trị Decimal chính xác cao sẽ bị format/downcast; source result và KPI không còn cùng biểu diễn đầy đủ giá trị. Holdout số nhỏ không đủ để chứng minh trường hợp này.

**Sửa tối thiểu:** thống nhất biểu diễn số chính xác ở QueryResult/renderer. Với KPI, dùng chuỗi hiển thị được tạo deterministic từ giá trị gốc hoặc formatter có giới hạn rõ ràng và lưu cả raw/display value; nếu Plotly Indicator không bảo toàn được Decimal thì dùng annotation/text trace thay vì âm thầm round. Ghi caveat nếu kiểu dữ liệu nguồn không thể bảo toàn.

**Test hồi quy:** integer lớn, Decimal có >12 significant digits, số âm, zero, scientific notation; so sánh raw evidence, JSON artifact và text người dùng thấy. Không phá rule KPI đúng một row.

### F5 — P2/Must quality gate: Mann–Whitney và Kruskal–Wallis không ghi assumption

**Điều khoản:** Spec.md:198-205 và Gate 6 Spec.md:444 yêu cầu kiểm tra assumptions áp dụng, sample/missing, effect size, multiple testing và phân biệt practical/statistical significance.

**Bằng chứng:** statistics/engine.py:199-215 (_mann_whitney) và statistics/engine.py:276-292 (_kruskal_wallis) gọi _tested_payload mà không truyền assumptions. statistics/models.py:218 cho phép tuple rỗng nên kết quả vẫn được publish.

**Tái hiện offline:** trên 6 dòng value=[1..6], group=[A,A,A,B,B,B], analyze cho cả hai operation trả assumptions=().

**Sửa tối thiểu:** thêm các check áp dụng với status/message rõ ràng, hoặc explicit NOT_CHECKED kèm lý do và caveat nếu một assumption không thể kiểm deterministic, ví dụ independence. Không gắn check giả là PASSED. Bổ sung kiểm Gate 6 trước publish.

**Test hồi quy:** mỗi StatisticalOperation có assumption payload phù hợp; failed/NOT_CHECKED được hiển thị; multiple testing và effect size vẫn giữ behavior hiện tại.

### F6 — P2/Must contract: CSV không truyền/kiểm MIME hoặc content signature

**Điều khoản:** Spec.md:106 yêu cầu validate extension, MIME/file signature, size, encoding, delimiter và structural readability.

**Bằng chứng:** streamlit_app.py:917-927 truyền chỉ uploaded.name và bytes vào stage_upload; application/service.py:116-142 cũng chỉ nhận filename/content; data/models.py:31-39 không có MIME field; data/core.py:103-141,626-655 suy ra CSV từ extension rồi decode/sniff/parse. XLSX có ZIP signature riêng ở data/core.py:324-347, nhưng CSV không có tương ứng.

**Tình huống:** file có đuôi .csv nhưng content-type do browser cung cấp khác hoặc content là một loại text/binary khác vẫn đi vào CSV detector; rejection hiện dựa vào parse/NUL/header chứ không ghi nhận MIME/signature evidence.

**Sửa tối thiểu:** đưa MIME nếu browser cung cấp và kết quả content sniff vào StagedUpload/UploadInspection; định nghĩa rõ CSV không có magic bytes nên structural/text signature là bằng chứng thay thế. Reject mismatch đáng ngờ với thông báo actionable; giữ XLSX ZIP checks.

**Test hồi quy:** valid UTF-8/UTF-8 BOM/legacy CSV, wrong MIME, binary masquerading as CSV, XLSX renamed CSV, empty/malformed header và actionable error. Đây là contract hardening, chưa có reproduction cho data loss.

### F7 — P2 lifecycle/UI: “Dismiss this question” không hủy durable checkpoint

**Điều khoản:** Spec.md:97-102, Spec.md:133-139, Spec.md:312-317, Spec.md:509-515 yêu cầu trạng thái phiên rõ, resume từ checkpoint và xử lý semantic pause/corrected request an toàn.

**Bằng chứng:** graph rejection path orchestration/graph.py:394-404 vẫn trả AWAITING_SEMANTIC_REVIEW. streamlit_app.py:517-531 khi bấm dismiss chỉ đặt st.session_state.agent_state = None rồi rerun; không gọi application.resume, sync_workspace hay ghi cancellation. application/service.py:310-335 chỉ project state vào workspace khi được gọi. Sau đó open_session ở application/service.py:217-253 thấy session waiting/active goal và phục hồi checkpoint cũ.

**Phạm vi tác động:** test tests/test_orchestration.py:1139-1150 chứng minh một request mới có thể thay thế pending clarification, nên đây không phải lỗi khiến người dùng vĩnh viễn không thể tiếp tục. Lỗi xuất hiện nếu user dismiss rồi đóng/reload trước request mới: câu hỏi cũ có thể hiện lại.

**Sửa tối thiểu:** hoặc gọi một transition cancel/refuse typed rồi sync checkpoint, hoặc đổi nhãn/hành vi thành “ẩn tạm thời” và không tuyên bố đã hủy. Thêm UI integration test dismiss → reload → không resurrect, hoặc kiểm tra chủ ý resurrect.

## 5. Ma trận ranh giới kiến trúc, lifecycle và bảo mật

### 5.1 Kiến trúc và dependency direction

| Điều khoản | Bằng chứng | Kết luận |
|---|---|---|
| UI là delivery adapter, core không phụ thuộc Streamlit | streamlit_app.py là entry point; rg trên src/tabular_analytics_agent không tìm thấy import streamlit | **Đạt về code.** Core có thể gọi bằng test/application service. |
| Public boundary dùng typed Pydantic contracts | domain/models.py:20-24; data/models.py:17-19; orchestration/models.py:38-39; model gateway và export đều extra=forbid, frozen | **Đạt.** Không nên bỏ model chỉ vì số lượng file lớn. |
| LangGraph sở hữu state/branch/retry/checkpoint/HITL | orchestration/graph.py:158-206,1150-1206; orchestration/checkpoint.py; graph.py:363-461 interrupt semantic và 579-610 plan approval | **Đạt về thiết kế; manual restart chưa kiểm chứng trong đợt này.** |
| Provider types không leak ra ngoài gateway | Chỉ model_gateway/gemini.py import LangChain message/client; application/orchestration nhận BaseModelGateway/typed responses | **Đạt về code.** |
| Temporary/atomic publication và artifact lifecycle | application/service.py:467-478; visualization/artifacts.py:89-103,137-169; _list_current 373-401 lọc dataset/version/fingerprint | **Đạt về code.** Không có bằng chứng stale dashboard bug ở đường này. |
| Không opaque ReAct loop | explicit named nodes/routes trong graph.py | **Đạt; độ dài 1,277 dòng là heuristic bảo trì, chưa phải overengineering đã chứng minh.** |

### 5.2 Session/lifecycle

- Create/upload tạo UUID session, path được derive từ UUID và source dataset ID riêng (application/service.py:116-175).
- Open/reload kiểm workspace identity, checkpoint, dashboard metadata (application/service.py:217-253,337-372,480-559).
- start/resume dùng SQLite checkpointer theo session và save state projection (application/service.py:281-335,449-465).
- Delete yêu cầu UUID canonical + tree validation, xóa artifact metadata rồi session tree và báo lỗi nếu bước sau fail (application/service.py:255-279). Đây đúng chủ ý Spec: tuần tự, không transaction, không forensic erase.
- F7 là điểm lifecycle còn lại. Không gộp nó với stale artifact: artifact store và export đều có current version/fingerprint checks.

### 5.3 Security/resource controls

| Kiểm soát | Bằng chứng | Đánh giá |
|---|---|---|
| Path/source immutability/hash | data/core.py:143-198 copy temp, hash recheck, atomic replace, read-only source; core.py:431-477 verify source hash/working metadata; service path guards 480-559 | **Đạt về code.** |
| Untrusted filename/content/prompt delimiting | filename sanitized application/service.py:600-610; prompt escaping/delimiters orchestration/prompts.py:20-113; profile sample caps/PII withholding | **Đạt; PII là heuristic.** |
| PII sample policy | orchestration/prompts.py:61-97; application/settings.py; docs/limitations.md:39-58 | **Đạt theo heuristic/option.** Tiếng Việt, tên người/địa chỉ không nhận diện được đã công khai; cần user hiểu provider vẫn nhận metadata/result values. |
| One read-only SQL over allowlisted table | data/sql_policy.py:79-175,188-223; data/core.py:236-322,394-429 | **Đạt nền tảng.** F2 là thiếu provenance scope; qualified id edge case được ghi ở FR-08. |
| DDL/DML/COPY/ATTACH/INSTALL/LOAD/filesystem/network rejection | data/sql_policy.py:108-175; DuckDB enable_external_access=false ở core.py:423-429; deny-list external functions | **Đạt theo static policy; chưa fuzz toàn bộ DuckDB function surface.** |
| Timeout/memory/output rows/budgets | data/models.py:21-28; core.py:256-322,403-429; orchestration/run_state.py; application/settings.py | **Đạt về code; full stress test do nhánh evaluation phụ trách.** |
| Credentials/traces | streamlit_app.py:72-87,951-963; model_gateway/gemini.py; traces chỉ giữ metadata/template version | **Đạt theo code review.** Không đọc secret/live API trong review. |
| At-rest encryption/authentication | docs/limitations.md:91-97 ghi local single-user, unencrypted files | **Giới hạn triển khai đã công khai**, không phải Must contradiction của local MVP; cần giữ trong release caveat. |

## 6. Ma trận statistics

| Operation | Source | Kết luận |
|---|---|---|
| Descriptive, CI | statistics/engine.py:84-135 | Có numeric cleaning, sample/missing và CI; CI có normality check. |
| Correlation | engine.py:138-168 | Pearson p-value + Spearman descriptive, effect, normality và non-causal caveat. |
| T-test | engine.py:171-196 | Welch t, group means, Cohen d, normality checks. |
| Mann–Whitney | engine.py:199-215 | Tính statistic/p/effect/medians nhưng không có assumptions tuple (F5). |
| Chi-square | engine.py:218-246 | Expected-cell assumption và Cramér's V. |
| ANOVA | engine.py:249-273 | Normality từng group + variance check + eta squared. |
| Kruskal–Wallis | engine.py:276-292 | Tính statistic/p/effect/medians nhưng không có assumptions tuple (F5). |
| Linear/logistic regression | engine.py:295-388,542-655 | Có typed estimates, p/effect và checks riêng; cần giữ caveat non-causal. |
| Common output policy | statistics/models.py:201-235; engine.py:69-81 | Result typed, p-value phải có named statistic/alpha/significance; Bonferroni warning khi multiple tests. |

_MIN_SAMPLE_SIZE=3 (engine.py:30) và typed InsufficientSampleError được nối vào refusal path trong graph. Chưa chạy full statistical suite; F5 được xác nhận bằng offline call nhỏ như trên.

## 7. Ma trận Verification Gates

| Gate trong Spec.md:437-448 | Đường triển khai | Trạng thái |
|---|---|---|
| 1. Field tồn tại | verification/query_evidence.py:22-50; verification/insights.py:112-115 | **Đạt cho non-empty fields; F1 cần exception có typed proof.** |
| 2. Type phù hợp | statistics/service.py:141-156; visualization/renderer.py:257-273 | **Đạt về code.** |
| 3. Filter/group/aggregation trong action | data/sql_policy.py:188-223; QueryResult.group_by_columns/filters; verification/insights.py:287-293 | **Một phần: F2 mất nested filter.** |
| 4. Source/result row counts | verification/insights.py:296-297; data/models.py:65-86 | **Đạt với result có field/evidence; F1 cần whole-dataset count scope.** |
| 5. Returned values khớp output | verification/insights.py:49-92,323-434; orchestration/evidence_catalog.py:166-242 | **Đạt về code; model semantic correctness vẫn không được chứng minh bởi gate.** |
| 6. Assumptions/sample/missing | statistics/models.py:201-235; statistics/engine.py | **Một phần do F5.** |
| 7. Claim không vượt evidence | verification/insights.py:323-434; domain/models.py:361-377 | **Đạt nền tảng.** |
| 8. Chart exact result | visualization/renderer.py:70-99,198-228 | **Một phần do F3 khi annotation khác rỗng.** |
| 9. Freshness | verification/insights.py:254-275; visualization/artifacts.py:373-401; application/exports.py:98-123 | **Đạt về current dataset/version/fingerprint path; không báo stale bug giả định.** |
| 10. Evidence Trail complete/reproducible | domain/models.py:331-358; verification/insights.py:203-220; export 132-165 | **Một phần do F1/F2.** |

## 8. UI và export

### UI

- Upload/inspect/sheet select/ingest: streamlit_app.py:909-947.
- Session selector, open, selected-session confirmation/delete, new session: streamlit_app.py:154-223.
- Profile/Data Overview tách khỏi Verified Insights: streamlit_app.py:381-438; deterministic overview không gọi model ở application/overview.py:62-126.
- Plan/mapping/approval/audit: streamlit_app.py:441-561,804-905; Audit hiển thị exact SQL/stat params, fields, verification, error/retry, insight scope/evidence và model metadata.
- Candidate/Pinned tách riêng và user pin/unpin: streamlit_app.py:773-801; store transitions artifacts.py:171-200.
- UI text hiện chủ yếu tiếng Anh. Vietnamese goal/header/value được hỗ trợ; Vietnamese user-interface text là Should theo Spec.md:58-60, nên không tạo Must blocker.
- docs/mvp-acceptance.md:38-46 xác nhận goal suggestions/overview đã có nhưng manual Streamlit review của upload → semantic → approval → result → chart → pin/open/restart/export/delete còn chưa hoàn tất. Đây là **chưa kiểm chứng**, không phải kết luận code không có.
- Screenshots, demo video, sample traces và case studies được user duyệt hoãn; docs/mvp-acceptance.md:62-67 ghi rõ chúng không phải Section 21 acceptance criteria. Không tạo Must blocker cho các mục này.

### Export

| Export contract | Bằng chứng | Trạng thái |
|---|---|---|
| CSV result tables | application/exports.py:168-196; UI streamlit_app.py:650-671 | **Đạt về code:** formula-like text/header được thêm apostrophe và bytes là UTF-8 BOM. |
| JSON Verified Insight/Evidence metadata | exports.py:132-165,199-213 | **Đạt về code:** query rows bị loại khỏi JSON; schema/SQL/count/stat params giữ lại; không prompts/traces/provider settings. |
| Current session/dataset/version/annotation binding | exports.py:85-93,98-124; visualization/artifacts.py:373-401 | **Đạt đường current check đã đọc.** QueryResult version được kiểm trực tiếp; StatisticalResult version đi qua Tool Action và nên được harden bằng test action status/version. |
| HTML report | docs/mvp-acceptance.md:69-71 | **Should, được chấp thuận hoãn.** |

Một hardening test nên xác nhận exporter từ chối action FAILED/verification fail hoặc action version cũ dù output ref trùng. application/exports.py:100-123 hiện kiểm action tồn tại, output ref và QueryResult version trực tiếp; đây là đề xuất P2 về defense-in-depth, chưa có reproduction từ đường UI bình thường.

## 9. Kế hoạch sửa tối thiểu và tiêu chí hoàn thành

Ưu tiên theo dependency, không vá riêng câu holdout:

1. **P1 — hợp đồng row count (F1).** Thêm typed whole-dataset count scope, nới đúng ngoại lệ cho COUNT(*), cập nhật verification/evidence/export và test end-to-end.
2. **P1 — filter provenance (F2).** Thu thập nested WHERE/HAVING theo AST scope, lưu canonical filter và test direct/CTE/subquery/HAVING.
3. **P1 — chart semantic binding (F3).** Hợp nhất nguồn fingerprint ở renderer/graph/artifact, test semantic confirm và stale annotation.
4. **P1 — KPI precision (F4).** Quyết định representation exact của Decimal/numeric và formatter display; test high-precision one-row KPI.
5. **P2 — statistics assumptions (F5).** Bổ sung checks hoặc NOT_CHECKED có lý do cho Mann–Whitney/Kruskal–Wallis; thêm Gate 6 regression.
6. **P2 — ingestion MIME contract (F6).** Ghi nhận browser MIME/content sniff; định nghĩa structural signature CSV; test masquerading files.
7. **P2 — semantic dismiss (F7).** Persist cancellation hoặc đổi nhãn thành hide; test reload behavior.
8. **P2 — qualified field id và pooled semantic scope.** Tiếp tục theo docs/review-overfit-architecture.md và docs/limitations.md; cần typed scope/evaluation paraphrase, không thêm keyword case riêng.
9. **Sau các P1/P2:** hoàn tất manual Streamlit acceptance, Docker build và live evaluation theo nhánh evaluation; không dùng điểm holdout cũ để thay thế nghiệm thu hiện tại. HTML, stacked chart, Vietnamese UI, transformations, local model và PDF/notebook giữ ở Should/Could theo Spec.

## 10. Kiểm chứng đã thực hiện trong nhánh này

- Offline chart validation dùng fixture hiện có: no annotation pass; confirmed annotation làm source_result_binding=False.
- Offline SQL policy: direct WHERE amount > 10 trả filters=('amount > 10',); equivalent CTE trả filters=().
- Offline statistics: Mann–Whitney và Kruskal–Wallis trên dữ liệu 6 dòng trả assumptions=().
- Không chạy full suite/live API/Streamlit/Docker trong nhánh này; không thay đổi source.
