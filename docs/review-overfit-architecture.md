# Rà soát overfit và overengineering — kiến trúc agent

Ngày rà soát: 2026-09-15  
Mốc mã: HEAD c6f703f2c7a06d1430aa6b3258c7fbeeef53e8d9  
Phạm vi: prompt, LangGraph orchestration, ModelGateway, ranh giới domain, field-id/SQL binding, logic trùng và hành vi đặc thù holdout.

## Cách đọc kết quả

Rà soát đã đọc toàn bộ Spec.md (kể cả các amendment đã duyệt), CONTEXT.md, docs/RESUME-HANDOFF.md và docs/review-plan.md. Knowledge graph của project hiện chỉ có 36 file và không biết các module mới trong src; vì vậy graph được dùng để định vị hotspot, sau đó nguồn hiện tại được kiểm tra bằng file discovery và đọc trực tiếp. Không sửa source, fixture hay kỳ vọng holdout; không gọi live API và không đọc secret.

Các nhãn trong báo cáo:

- **Xác nhận (confirmed):** có đường đi deterministic hoặc tái hiện offline.
- **Khoảng trống/rủi ro:** code cho thấy hợp đồng chưa rõ hoặc có rủi ro, nhưng chưa đủ để gọi là lỗi sản phẩm hiện tại.
- **Heuristic:** nhận xét về khả năng bảo trì hoặc prompt; cần đánh giá hành vi trước khi sửa.
- **Spec-required/limitation:** complexity hoặc giới hạn được yêu cầu hay chấp thuận trong Spec, không phải overengineering.

## Kết luận điều hành

Kiến trúc hiện tại không cho thấy một vấn đề overfit ở cấp hệ thống và không cần big rewrite. Typed contracts, workflow state rõ ràng, checkpoint/HITL, deterministic verification, evidence budget, stable field ids và quota-aware key rotation đều có điều khoản cụ thể trong Spec. Xóa các lớp này chỉ vì file lớn sẽ làm mất các cổng an toàn và khả năng tái lập mà MVP yêu cầu.

Có hai lỗi đã xác nhận cần sửa theo thứ tự ưu tiên:

1. SQL field id có qualifier, ví dụ d.c5 trong CTE, không được rewrite và có thể làm hết repair budget.
2. Chart Intent có semantic annotation fingerprint bị renderer đối chiếu với reference không có annotation, nên chart bị từ chối sau khi người dùng xác nhận semantic.

Có hai rủi ro bảo trì nhỏ hơn:

3. Hai hàm fingerprint semantic dùng hai thuật toán sort khác nhau.
4. PlanDraft giữ hard cap 12 bước trong khi ExecutionBudget cho phép cấu hình max_tool_actions.

Pooled query và các câu hỏi ranking mơ hồ là giới hạn semantic/prompt đã được ghi nhận, chưa phải bằng chứng rằng code đang overfit vào một fixture. Sửa tối thiểu nên bổ sung hợp đồng và evaluation cho scope kết quả, không thêm nhánh từ khóa cho đúng câu holdout.

## Complexity hợp lệ theo Spec — không refactor bỏ

| Thành phần | Bằng chứng trong mã | Đối chiếu Spec và đánh giá |
|---|---|---|
| Workflow explicit | orchestration/graph.py:237-249, 1150-1206 | Spec.md:286-319 yêu cầu LangGraph sở hữu state, branching, retry, checkpoint, HITL và cấm opaque ReAct loop. 8 node lồng trong một file là chi phí bảo trì, nhưng graph explicit là Must. |
| Typed boundaries | orchestration/models.py:55-126; model_gateway/base.py:27-95 | Spec.md:286-292, 323-344 và 25.2 yêu cầu Pydantic contracts, provider-neutral gateway, typed assertion/evidence. Không bỏ các model để “đơn giản hóa”. |
| Verification và headline stats | orchestration/graph.py:969-1010; verification/insights.py:330-566 | Spec.md:207-225 yêu cầu claim deterministic từ typed assertion và tự bổ sung mỗi group mean hoặc coefficient mà model bỏ sót. Logic headline không phải dead code hay prompt patch. |
| Field ids và SQL AST policy | orchestration/field_ids.py:24-90; data/sql_policy.py:44-276 | Spec.md:514 và 25.1 yêu cầu c1/c2, r1/r2, quoted exact names, alias deterministic và read-only AST policy để hỗ trợ tên Unicode an toàn. |
| Evidence/prompt bounds | orchestration/evidence_catalog.py:32-158; orchestration/prompts.py:20-113 | Spec.md:452-497 giới hạn sample values, PII, query rows và prompt size. Nhiều hằng số ở đây là data-exposure/resource controls, không phải abstraction dư. |
| Gateway repair, timeout, key rotation | model_gateway/base.py:35-159; model_gateway/gemini.py:96-337 | Spec.md:406-413 và 501-516 yêu cầu Fake gateway, một repair schema, timeout, provider-error classification và rotation 15 RPM/65 giây. Không thay bằng provider fallback hay một client duy nhất. |

graph.py có 1.277 dòng và chứa các node từ 249 đến 1148. Đây là **heuristic bảo trì**, không phải bằng chứng overengineering: mọi node/route ở đây tương ứng với state machine bắt buộc. Việc tách file chỉ nên làm sau khi sửa lỗi hành vi, từng node một, vẫn giữ tên node, state serialization và checkpoint schema.

## Phát hiện xác nhận 1 — P2 reliability: qualifier d.c5 không được rewrite

### Bằng chứng

data/sql_policy.py:44-69 mô tả và triển khai rewrite field id. Vòng lặp lấy Column ở dòng 62-64 nhưng dòng 65 bỏ qua khi column.table có giá trị:

    if target is None or column.table or name.casefold() in aliases:
        continue

Vì vậy lookup c5 chỉ thay cột không có qualifier. Tái hiện offline:

    input:
      WITH raw AS (SELECT c5 FROM dataset)
      SELECT d.c5 FROM raw AS d
    replacements:
      {"c5": "Kênh đặt"}
    output:
      WITH raw AS (SELECT "Kênh đặt" FROM dataset)
      SELECT d.c5 FROM raw AS d

Trong docs/mvp-acceptance.md:227-230 và docs/limitations.md:171-178, holdout v9 đã gặp đúng nhánh này: một pooled-average query viết d.c5 trong CTE, query fail và repair budget hết. Đây là lỗi xác nhận ở boundary, dù câu chữ Spec.md:768 hiện nêu tối thiểu “unqualified field ids”.

### Tác động người dùng

Model có thể dùng id ASCII đúng theo profile nhưng thêm qualifier hợp lệ cho alias CTE. Người dùng nhận failure thay vì kết quả; một retry nữa cũng có thể không còn vì action repair budget đã bị tiêu thụ. Lỗi không phụ thuộc tên “Kênh đặt”; tên Unicode chỉ làm hậu quả dễ thấy hơn.

### Refactor tối thiểu

Sửa binding bằng traversal scope-aware của SQLGlot: nếu qualifier là alias của CTE/table đang chứa field id, rewrite source column theo scope và giữ qualifier/alias đúng nghĩa. Không dùng replace chuỗi cho mọi token; không đổi output alias, không biến một alias hợp lệ thành source field. Nếu chưa hỗ trợ scope an toàn, lựa chọn an toàn thứ hai là từ chối qualified id với lỗi rõ để model retry bằng tên/id unqualified.

### Acceptance tests

1. Query CTE có d.c5 rewrite và execute thành công với tên field Unicode.
2. Qualified field không thuộc allowlist vẫn bị reject.
3. Alias tính toán như AS c5 hoặc AS total không bị rewrite thành tên source.
4. c5 unqualified, mixed case và tên thật trùng c5 vẫn giữ các quy tắc đang pass.
5. Case trên không tạo thêm repair khi query sau rewrite hợp lệ.

## Phát hiện xác nhận 2 — P1: semantic annotation làm chart binding fail

### Bằng chứng

Khi synthesize/propose artifact, graph lấy annotation hiện tại và tạo reference có fingerprint ở orchestration/graph.py:940-943 và 1103-1106:

    source_result_ref = make_query_result_reference(result, annotations)

make_query_result_reference ở visualization/renderer.py:57-67 thực sự ghi fingerprint vào QueryResultReference. Nhưng validate_chart_intent ở renderer.py:70-99 tạo:

    expected_ref = make_query_result_reference(result)

Đối số annotations bị bỏ trống; so sánh source binding ở dòng 97 vì thế đòi intent.source_result_ref phải có fingerprint của tuple rỗng. Khi annotations đã được user xác nhận, hai reference không thể bằng nhau.

Tái hiện offline bằng QueryResult và ToolAction fixture hiện có cho kết quả:

    failed
    [('source_result_binding', False,
      'Chart Intent or Tool Action does not reference this Query Result.')]

graph.py:1125-1146 bắt ChartValidationError, để chart_renders rỗng và artifact_error có nội dung, nhưng status vẫn COMPLETED ở dòng 1138. Đây là lỗi user-visible: insight có thể đã verified nhưng chart biến mất sau bước propose artifact.

Test hiện có chưa bắt đường đi này. tests/test_orchestration.py:591-621 kiểm semantic review nhưng fake response không đi tiếp với chart; các chart test tests/test_orchestration.py:559-588 và tests/test_visualization.py:61-66 dùng reference với annotations rỗng.

### Refactor tối thiểu

Truyền tuple annotations hiện tại từ session đáng tin cậy vào validate_chart_intent/render_chart. Không lấy expected fingerprint từ chính intent đang kiểm tra vì đó là kiểm tra vòng tròn. Giữ kiểm tra exact query id, dataset id, working version và fingerprint; Spec yêu cầu annotation change làm result/artifact stale. Hợp nhất hai fingerprint helper là việc P2 riêng, không phải điều kiện cần để sửa chart.

### Acceptance tests

1. End-to-end: model đề xuất annotation, user confirm, query verified, chart bar/table được render và không có artifact_error.
2. Cùng query nhưng semantic annotation đổi phải làm reference stale và chart bị từ chối.
3. Khác query_id/dataset/version hoặc Tool Action chưa verified vẫn bị từ chối.
4. Chart không được nhận cell values hoặc tự aggregation; các gate hiện tại vẫn phải pass.

## Rủi ro hợp đồng fingerprint — P2, chưa gọi là lỗi runtime độc lập

Có hai triển khai cho cùng khái niệm fingerprint:

- domain/models.py:425-434 sort theo field_name phân biệt hoa thường rồi JSON serialize.
- verification/insights.py:39-46 sort theo field_name.casefold() và JSON tie-break.

Call sites cũng tách đôi: visualization/renderer.py:22,66 và visualization/artifacts.py:22,395,544 dùng helper domain; insight publication/invalidating và application/exports.py:24,99,112,148 dùng helper verification. Với cùng hai annotation field_name a và B, hai helper hiện cho hash khác nhau:

    domain:      075331b342c37cbb7d74b4e5ddb889752f46b168e7afe7e53d2432b9e8aa3167
    verification: 8cd9acd3066d63bb28f119f3238cb27d21d5a73f07657243219a21c061a3e5b7

Đây là maintenance/compatibility risk vì QueryResultReference và EvidenceTrail đều nói về semantic annotations nhưng có thể không so sánh được giữa boundary. Hiện chưa đủ bằng chứng để khẳng định artifact path đang hỏng: mỗi nhóm caller đang nhất quán nội bộ. Cần quyết định một canonical algorithm, hoặc ghi rõ hai namespace/version khác nhau.

### Acceptance và dữ liệu cũ

1. Một helper canonical duy nhất được import từ cả domain, verification, visualization và export.
2. Test đổi thứ tự annotations không đổi hash; test tên Unicode và tên khác hoa thường có kết quả được quy định rõ.
3. Test cùng annotation tuple cho QueryResultReference, EvidenceTrail, VerifiedInsight export và ArtifactStore cho cùng fingerprint.
4. Không đổi im lặng hash đã lưu. Nếu chọn thuật toán mới, thêm fingerprint/schema version và migration hoặc giữ thuật toán cũ cho dữ liệu v1 rồi chuyển dần.

## Rủi ro cấu hình và logic trùng — P2

### Hard cap plan không đồng bộ budget

model_gateway/schemas.py:143-155 từ chối PlanDraft có hơn 12 steps. Trong khi domain/models.py:230-235 khai báo ExecutionBudget.max_tool_actions configurable và Spec.md:469-477, 802-803 nói limits phải configurable. Với max_tool_actions=20, model vẫn không thể nhận plan 13 bước; với budget nhỏ hơn 12, schema vẫn nhận kế hoạch dài rồi orchestration mới chặn. Mặc định 12 là đúng Spec, nên đây là mismatch ẩn chứ chưa phải lỗi của default.

Refactor nhỏ: quyết định rõ 12 là hard safety ceiling hay budget có thể tăng; đưa policy vào một constant/helper dùng chung, và hiển thị effective limit. Không cần context injection phức tạp vào Pydantic schema.

Acceptance:

1. Budget mặc định giữ hành vi 12.
2. Budget nhỏ hơn 12 bị chặn ở cùng một boundary với thông báo effective limit.
3. Budget lớn hơn 12 hoặc được hỗ trợ xuyên suốt, hoặc bị từ chối sớm với lý do hard ceiling; không để model tưởng rằng giá trị 20 có hiệu lực.
4. Test audit/failure message hiển thị giới hạn thực tế.

### Inferential operations và alpha bị lặp

orchestration/binding.py:40-52 tự liệt kê INFERENTIAL_OPERATIONS và INFERENCE_ALPHA=0.05; cùng chính sách operation nằm trong statistics/models.py:61-116 và alpha mặc định ở statistics/models.py:149-162. Đây chưa gây sai số hiện tại vì test orchestration vẫn kiểm adjusted_alpha 0.05, nhưng thêm operation mới có thể cập nhật một nơi mà quên nơi kia.

Refactor nhỏ: expose helper chính sách từ statistics (ví dụ operation rule có cờ inferential và alpha policy), để binding chỉ gọi helper thay vì copy set. Giữ family-size correction và test tất cả StatisticalOperation.

Acceptance:

1. Một bảng operation duy nhất điều khiển prompt guide, request validation và binding.
2. Thêm operation test fixture chỉ cần cập nhật một policy table.
3. Inferential family size, adjusted alpha và rejection behavior không đổi trên fixture hiện tại.

## Prompt và semantic scope — giới hạn cần đo, không vá theo câu holdout

### Pooled query

plan-v8 ở orchestration/graph.py:478-497 và tool guidance ở orchestration/prompts.py:44-57 đã giải quyết một vấn đề khác: các SQL step không đọc kết quả của nhau; truy vấn phụ thuộc phải dùng một CTE/subquery. Chúng chưa định nghĩa scope “counted together”, “pooled” hay “overall”. Vì vậy model có thể viết đúng một CTE nhưng vẫn giữ GROUP BY industry và trả một churn rate cho mỗi industry.

docs/mvp-acceptance.md:227-230 ghi cả ba run pooled v9 đều bị nhóm theo industry; docs/limitations.md:173-178 mô tả cùng giới hạn. Đây là observed prompt/semantic limitation, chưa phải deterministic bug trong SQL executor. User-visible scenario là hỏi một tỷ lệ duy nhất trên ba ngành nhưng nhận ba tỷ lệ riêng.

Refactor tối thiểu theo thứ tự:

1. Thêm golden cases unseen cho pooled aggregate, per-group aggregate và “top group rồi tính metric”, mỗi case khai báo expected group_by shape và expected metric scope.
2. Đo output plan/SQL trước khi đổi prompt. Nếu “one rate across all groups” vẫn sai, thêm một trường scope typed ở plan hoặc một clarification rule tổng quát; không thêm if cho từ “counted together” của fixture v9.
3. Validator nên kiểm tra invariant có thể xác định được (ví dụ pooled result không có group dimension) và cho model retry với lỗi cấu trúc; việc hiểu câu tự nhiên vẫn thuộc evaluation.
4. Giữ prompt version mới và so sánh holdout cũ, holdout sạch và case paraphrase; không sửa kỳ vọng sau khi thấy kết quả.

### Các ví dụ domain trong prompt

semantic-v13 ở orchestration/graph.py:280-298 nêu orders, readings, tickets và late returns cho quy tắc row count; plan-v8 ở dòng 485 cũng nêu orders/tickets. Đây là một heuristic có nguy cơ làm prompt dài hơn, nhưng không phải bằng chứng overfit: Spec revision history v1.4 (Spec.md:758) nói rõ mục tiêu là counting records “under any name”. Chỉ rút gọn thành “records/rows/entity names” sau khi evaluation với paraphrase unseen cho thấy không giảm chất lượng. Không xóa các quy tắc bảo vệ COUNT(*) và không thêm danh sách holdout nouns mới.

### Prompt safety và traceability

orchestration/prompts.py:20-113 escape untrusted content, giới hạn sample values và ghi field ids; graph.py:302, 501, 641, 906, 1088 ghi prompt-template version. Đây là required reproducibility/PII behavior theo Spec.md:489-535. Không gom prompt thành một “mega prompt” hoặc bỏ version chỉ để giảm số dòng.

## Holdout: không kết luận overfit từ điểm giảm

| Đợt | Kết quả được ghi nhận | Cách diễn giải |
|---|---:|---|
| v7 sạch | 36/36, sau các root fix được duyệt | Một lần chạy 12 case, không phải mẫu lớn; chứng minh các fix lúc đó hoạt động trên set đó. |
| v8 hard measurement | 24/36 | Dataset khác; docs/mvp-acceptance.md:193-202 ghi không tìm thấy deterministic defect và giữ kết quả làm limitation. |
| v9 | 20/36 | docs/mvp-acceptance.md:222-237 ghi fix spelling đúng ở mọi run, CTE có mặt nhưng pooled scope sai, d.c5 fail, chart-only failures và vague ranking. |

v8 và v9 không phải cùng một release suite; score không so sánh trực tiếp. Những điểm giảm chưa chứng minh prompt overfit. Tín hiệu mạnh hơn là lỗi d.c5 có code path deterministic và pooled semantics có lặp lại nhưng chưa có scope contract. Evaluation cần giữ holdout contamination/version/grader metadata tách biệt.

## Backlog refactor tối thiểu, theo thứ tự

### 1. Sửa semantic chart binding — P1; hợp nhất fingerprint riêng ở P2

Phạm vi P1: truyền current annotations qua graph/renderer/validator. Giữ toàn bộ verification gates. Domain/verification fingerprint consolidation thực hiện riêng sau khi có quyết định tương thích dữ liệu.

P1 hoàn thành khi semantic chart end-to-end render được, annotations stale vẫn bị reject và persisted hashes không thay đổi. P2 consolidation chỉ hoàn thành khi có kiểm thử tương thích hash/version riêng.

### 2. Rewrite qualified field ids theo scope — P2 reliability

Phạm vi: data/sql_policy.py và test SQL policy. Dùng AST scope/qualifier hoặc reject rõ ràng với repair; không regex patch d. cho holdout.

Hoàn thành khi test CTE d.c5, alias preservation, allowlist rejection và execution không cần repair bất ngờ đều pass.

### 3. Đặt hợp đồng pooled/grouped và evaluation paraphrase — P2

Phạm vi trước tiên là fixtures/evaluation và prompt version; chỉ thêm typed scope hoặc validator sau khi test chứng minh gap còn tồn tại. Không đổi v9 expectation hồi tố.

Hoàn thành khi pooled, per-group và dependent CTE cases phân biệt được; một câu mơ hồ được clarification hoặc refusal có lý do, không tự chọn group scope.

### 4. Gom policy operation/alpha và làm rõ effective plan limit — P2

Phạm vi: statistics policy, orchestration binding, PlanDraft/ExecutionBudget. Không thay đổi kết quả thống kê hiện có.

Hoàn thành khi một policy table điều khiển các boundary và config >12 được hỗ trợ xuyên suốt hoặc bị từ chối sớm, có test thông báo limit.

### 5. Tách graph theo lát cắt hành vi — P3, chỉ khi vẫn cần sau các bước trên

Không rewrite LangGraph. Trích prompt builders hoặc từng node khỏi graph.py, giữ public build_agent_graph, node names, edge routes, AgentState JSON shape, checkpoint round-trip và prompt template versions. Mỗi bước phải giảm coupling/độ dài có đo được và giữ toàn bộ suite.

Acceptance: test orchestration hiện tại, SQLite checkpoint resume, semantic/HITL pause, provider error classification và trace sequence giữ nguyên. Nếu không có lợi ích đo được, để graph.py nguyên trạng.

### 6. Gateway timeout resource test — P3, evidence gap

model_gateway/gemini.py:315-337 chạy request trong daemon thread và khi timeout chỉ dừng chờ; underlying runnable có thể còn chạy. Spec yêu cầu hard call timeout, nhưng chưa có live evidence về thread accumulation và không nên tự ý thay transport. Trước khi redesign, thêm fake blocking runnable test để đo timeout, concurrent calls và resource behavior; chỉ thay adapter khi test cho thấy leak ảnh hưởng thực tế.

## Những thứ trông như duplicate nhưng nên giữ

- AgentState có query_result/statistical_result hiện tại và các list query_results/statistical_results ở orchestration/models.py:92-126. Singularity phục vụ màn hình hiện tại; list phục vụ evidence/export/evaluation. Cần document invariant hoặc helper đọc state, không xóa một bên khi chưa kiểm tra consumer.
- ModelGateway Protocol và BaseModelGateway không phải duplicate vô ích: một cái là provider-neutral boundary, một cái giữ timeout/validation repair/trace chung.
- _headline_statistical_metrics không phải workaround holdout; đây là FR-10 và revision history đã phê duyệt.
- ASCII field ids, result ids và bounded evidence không phải hardcoded domain behavior; chúng là contract để tên Unicode, PII và resource limits hoạt động.

## Kiểm chứng đã chạy

- Python offline reproduction của qualified id: output giữ nguyên d.c5 như mô tả.
- Python offline reproduction của semantic chart: source_result_binding trả failed khi intent có annotation fingerprint.
- Python offline reproduction của hai fingerprint helper: cùng tuple a/B cho hai hash khác nhau.
- Test liên quan visualization/orchestration/artifacts: 87 passed trong 16.66 giây, dùng no-cov và no-cacheprovider; không gọi provider.

Không có source implementation nào được thực hiện trong đợt rà soát này.
