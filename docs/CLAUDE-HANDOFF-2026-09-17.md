# Bàn giao cho Claude — tiếp tục dự án Data Analyst Agent

Cập nhật: 2026-09-17. Tài liệu này tổng hợp quyết định, triển khai, kết quả và việc còn lại trong đoạn chat với Codex. Đây là context để tiếp tục làm việc, **không phải xác nhận dự án đã hoàn thành**.

## 1. Đọc phần này trước

- Workspace: `C:\Users\A Fee\Desktop\Workspace\data_analyst_agent`.
- Shell: PowerShell; Python dự án: `.\.venv\Scripts\python.exe`.
- Branch hiện tại: `codex/semantic-correctness`.
- HEAD: `8548f8d4f83b61dbc2b6495a4537622bdb7b69bd` — `chore: checkpoint full-data analysis contract`.
- **Các thay đổi semantic sau checkpoint vẫn chưa commit. Có cả source/test mới chưa được Git track. Không reset, clean hoặc chỉ đọc `git diff` rồi bỏ sót file untracked.**
- Mã hiện tại qua offline CI: **615 passed, 2 skipped; coverage 90,70%**.
- Nghiệm thu Gemini mới nhất chạy đủ: **release 92/120; holdout 27/30; cả hai không có lỗi provider hay provider retry**.
- **Chưa đạt nghiệm thu:** release trượt calculation/tool execution/chart/end-to-end; holdout trượt calculation.
- Đã sửa lỗi catalog/prompt làm model nhầm alias metric với evidence ID. Tuy nhiên vẫn còn 28 lượt release không đạt.
- Không còn runner/API chạy nền khi bàn giao. Không cần resume các session ID cũ.
- Không cần chạy lại toàn bộ từ đầu chỉ để khôi phục context. Đọc artifacts, chẩn đoán phần còn lại rồi mới thay đổi có kiểm chứng.

## 2. Mục tiêu và cách người dùng muốn làm việc

Người dùng ban đầu yêu cầu rà soát toàn bộ dự án: mức hoàn thiện, sai lệch Spec, overengineering, hardcode và vá theo từng case. Sau đó đã chốt plan, yêu cầu triển khai bằng **các subagent Luna Max (`gpt-5.6-luna`, reasoning `max`)**, agent chính chỉ giám sát, kiểm tra lại và tổng hợp.

Phạm vi gần nhất được giao là hoàn tất **phần 4 của đợt semantic correctness**:

1. Checkpoint phần full-data đã làm.
2. Sửa ngữ nghĩa phân tích tổng quát: grain/dedup, dữ liệu số/ngày dạng text, metric/group/unit/formula.
3. Sửa nguyên nhân upstream khiến thiếu chart; không viết lại chart framework.
4. Thêm regression semantic độc lập, chạy exact CI, release 40 case × 3 và holdout 10 case × 3, báo kết quả.

Docker/UI là phần tiếp theo, **không mở rộng sang phần đó trong đợt semantic vừa rồi**. Đợt full-data trước đã sửa cấu hình Docker nhưng host không có Docker CLI để nghiệm thu; không được gọi Docker/UI là đã kiểm chứng đầy đủ.

Người dùng nhiều lần tạm dừng/đổi tài khoản vì quota Codex rồi yêu cầu tiếp tục. Phải giữ công việc và artifacts, không tự chạy trùng runner. Những lần gián đoạn này không hủy mục tiêu.

## 3. Các quyết định đã khóa — không tự quyết định lại

### 3.1 Full-data là chế độ duy nhất của MVP

**Tính toán phải dùng toàn bộ dữ liệu trong phạm vi phân tích được duyệt; không có nghĩa là phải đưa tất cả dòng vào prompt hoặc hiển thị tất cả dòng.**

- Không reservoir sampling tự động, không fallback 10.000 dòng khi file lớn hoặc workload chậm.
- Sampling do người dùng chủ động chọn là tương lai, chưa triển khai trong MVP.
- `max_query_rows` chỉ giới hạn các dòng trả về/hiển thị, không giới hạn dữ liệu đầu vào aggregate/statistical calculation.
- Khi có filter đã được hỗ trợ: đọc/phân tích toàn bộ phần dữ liệu thỏa filter, không lấy mẫu phần đó.
- Không mở rộng filter API của `statistical_analysis` chỉ để phục vụ test. Test filter dùng SQL/analysis scope đã có.

Ý nghĩa metadata phải tách biệt:

| Trường | Ý nghĩa |
|---|---|
| `dataset_row_count` | Số dòng dataset gốc |
| `population_row_count` | Số dòng thuộc phạm vi filter/phân tích đã duyệt |
| `rows_loaded` | Số dòng thực sự đưa vào phép tính |
| `sample_size` | Số quan sát hợp lệ dùng cho phép thống kê sau xử lý missing; không đổi nghĩa thành số dòng dataset |
| `missing_row_count` | Số dòng bị loại vì thiếu dữ liệu theo phép tính |
| `sampled` | `false` cho kết quả MVP mới |
| `sampling_method`, `sampling_seed` | `null`/`None` |
| `partial`, `truncated` | `false` cho kết quả phân tích chính hợp lệ |

Full-data invariant: `rows_loaded == population_row_count`, `sampled == false`, `partial == false`, `truncated == false`. Preview query bị cắt số dòng không được bị nhầm thành aggregate chạy trên dữ liệu bị cắt.

Metadata phải lấy từ execution thực, không sao chép số đếm từ profile; truyền qua `StatisticalResult`, `ToolAction.inputs`, evidence catalog, `EvidenceTrail`, insight verification và JSON/export. Action mới không được còn ghi `method=reservoir` hoặc `maximum_rows=10000`.

### 3.2 Timeout và legacy evidence

- Dùng deadline/timeout profiling hiện có; không giảm phạm vi dữ liệu để né timeout.
- Python calculation phải nằm trong execution boundary có thể terminate thật. Không dùng thread chỉ để ngừng chờ trong khi CPU workload vẫn tiếp tục.
- Worker chỉ nhận dữ liệu/tham số serialize được, không nhận live DuckDB connection.
- Process cha sở hữu connection, file tạm và cleanup; Windows dùng `spawn`.
- Timeout phải dừng workload, đóng tài nguyên và cleanup; không trả partial result như thành công, không tạo insight/export hoàn tất từ workload thất bại.
- Evidence cũ từng sampling/không đủ scope phải giữ khả năng đọc và được đánh dấu legacy/unverified; không dùng để tạo publication/export mới khi chưa rerun Tool Action.
- Spec đã được sửa theo contract: `Runs on all rows in the approved analysis scope. Sampling is not used automatically by the MVP.`

### 3.3 Baseline và kiểm thử

> Giữ nguyên release 40 case và holdout 10 case làm baseline bất biến; chỉ bổ sung fixture/case semantic mới, không sửa expected hiện tại để làm tăng điểm.

- Không sửa case, expected hoặc fixture hiện có của release/holdout.
- Không dùng holdout để tune code, prompt, fixture hoặc expectation. Holdout đã chạy nhiều lần trong chat; không gọi lần chạy sau là một tập hoàn toàn chưa từng quan sát.
- Regression mới dùng dữ liệu synthetic độc lập, tên/schema khác; không mang đáp án hay tên case release vào production.
- Full-data regression 10.001 dòng phải kiểm tra giá trị thực tế lẫn metadata, missing/filter/timeout/cleanup, và không còn sampling ngầm.
- Không hạ coverage 90%, đổi threshold, thêm `pragma` để đạt điểm hoặc mở bypass cho `FakeModelGateway`.
- Được cập nhật payload **unit test Python** cho schema kế hoạch mới. Điều đó khác với sửa immutable evaluation baseline.
- Một unit test SQL metric giả đã được đổi sang retry SQL sai có giới hạn rồi `FAILED`, không có verified claim/chart/publication. Đây là thay đổi fail-closed có chủ đích, không giảm yêu cầu bảo vệ.

### 3.4 Quyền gọi Gemini và nhịp chạy

- Người dùng đã xác nhận cụ thể cho phép gửi **fixture datasets và prompts của release 40 × 3 cùng holdout 10 × 3 tới Gemini**, bao gồm chạy API thật.
- Người dùng yêu cầu nhịp bình thường **12 request/phút mỗi key**; không tự hạ còn 3 hoặc 6 như trạng thái mặc định mới.
- Lượt mới nhất dùng `gemini-3.5-flash-lite`, tám key được cấu hình. Không ghi/in key, `.env`, raw credentials.
- Quota Codex và lỗi Gemini API là hai thứ khác nhau. Đã từng xác nhận Gemini HTTP 429, retry delay 59 giây; **không đủ bằng chứng kết luận quota ngày**.
- Auto-review từng từ chối network trước khi có xác nhận payload cụ thể; sau xác nhận, lệnh network đã được duyệt và hai suite mới nhất chạy đủ không lỗi provider.
- Không vòng lặp retry vô hạn, không thay/luân chuyển cấu hình key để né giới hạn; dùng pacing và bounded retry có sẵn.

## 4. Những gì đã triển khai

### 4.1 Đợt full-data/provenance trước checkpoint

Chi tiết đối chiếu tại `docs/implementation-acceptance.md`.

- Bỏ sampling tự động và tách full statistical read khỏi preview row cap.
- Thêm/truyền metadata phạm vi dữ liệu và chặn legacy evidence trong publish/export mới.
- Timeout đọc/chuyển đổi/tính toán, process worker có thể terminate thật, parent cleanup.
- SQL provenance qua `QueryResult.inspection`: base relations và output dependencies; chặn CTE giả dataset, metric literal không có nguồn và hàm không tái lập trong phạm vi đã triển khai.
- Cập nhật Spec và cấu hình Docker; Docker thực tế chưa được xác minh trên host.
- Mốc này đã được checkpoint ở HEAD `8548f8d...`.
- Kết quả lịch sử của mốc này: offline 516 passed/2 skipped, coverage 90,94%; release 108/120, holdout 30/30. **Không dùng các số lịch sử này làm kết quả của source hiện tại.**

### 4.2 Normalization giữ nguyên dữ liệu gốc

- Thêm `data/normalization.py` và `FieldNormalization` trong `domain/models.py`; profile có normalization metadata tùy chọn để đọc được dữ liệu cũ.
- Phân tích toàn cột trong deadline. Phân loại `numeric`/`date`, trạng thái `ready`/`ambiguous`/`invalid`/`unsupported`, số lượng valid/missing/invalid/ambiguous, unit, expression, reason.
- Chỉ công bố SQL recipe khi có cách hiểu xác định và hợp lệ. Không dùng `TRY_CAST` để âm thầm loại dữ liệu lỗi khỏi phân tích chính.
- Giữ nguyên raw strings trong Source/Working Dataset. Một hướng coercion khi ingest đã bị loại sau regression; không đưa trở lại chỉ để test/type inference dễ qua.
- Ngày mơ hồ phải tiếp tục mơ hồ; không đoán day-first/month-first tùy case. Không tự đổi đơn vị hỗn hợp.
- `application/overview.py` dùng date recipe hợp lệ cho grouping/filter/date operations. Chuỗi số giống ID không tự mất cảnh báo identifier chỉ vì parse thành số được.

### 4.3 Typed metric contract và verification

- Thêm `MetricOperation`, `MetricGrain`, `MetricRequirement`.
- Operation hiện có: row count, distinct-value count, deduplicated-row count, sum, average, min, max, rate, ratio, top.
- Contract mô tả alias `metric_id`, grain, `source_fields`, `grouping_fields`, dedup keys/scope, numerator/denominator fields, scale/unit.
- Rate/ratio mới yêu cầu riêng `numerator_aggregation` và `denominator_aggregation` với lựa chọn `count` hoặc `sum`; không đoán mặc định count.
- **Đây là contract bị giới hạn, không phải ngôn ngữ mô tả mọi công thức. Cần xem lại khả năng biểu diễn đúng nhu cầu trước khi quy mọi rejection cho model.**
- `PlanStepDraft` mới bắt buộc contract cho SQL phân tích. Persisted domain `PlanStep`/plan cũ vẫn đọc được, không tự được cấp một công thức suy diễn.
- `non_metric` dành cho truy vấn hàng/structural thật sự; verifier chặn aggregate/GROUP/WINDOW/DISTINCT gắn cờ này để bỏ qua contract.
- Binding truyền contract vào plan/action, chuẩn hóa field/alias, kiểm tra field và output alias.
- Thêm `verification/semantic.py`: kiểm tra operation/grain/formula/dedup/normalization bằng AST trong phạm vi hỗ trợ, gộp vào verification hiện có; repair có giới hạn khi không đạt.

Các lỗi được phát hiện và đã thêm regression/sửa trong chat:

- Không chấp nhận `COUNT(*) + 999` hoặc `SUM(x) * 0` chỉ vì chứa COUNT/SUM.
- Không chấp nhận dedup nhờ một `SELECT DISTINCT` ở CTE không liên quan nguồn được đếm.
- Không bỏ qua GROUP BY chỉ vì cột nhóm không nằm trong projection.
- Không chấp nhận tử/mẫu tỷ lệ bị cộng hằng số hay đổi phép toán ngoài contract.
- Không chấp nhận `SUM(LENGTH(field))` như SUM giá trị field, hoặc conversion không được duyệt làm mất giá trị lỗi.
- So sánh normalization recipe bằng AST đã chuẩn hóa; không dùng API `.equals()` không tồn tại của SQLGlot hoặc nhầm `.eq()` tạo biểu thức SQL với phép so sánh boolean.
- Coverage insight theo cặp step/metric, không bỏ sót step chỉ vì step khác có cùng alias.
- Không mở FakeGateway validation bypass để cứu fixture kế hoạch cũ.

### 4.4 Lỗi alias gây 40 lượt không có insight — đã sửa

Ở freeze semantic đầu `4fe97433f7d2`, release có 40 lượt `completed` nhưng không có verified insight. Catalog/prompt cùng lúc đưa ra:

- `metric_requirements.metric_id` dạng alias trần;
- evidence ID thực tế của query dạng `row[index].column`.

Model chọn alias trần; provenance từ chối đúng. Identifier repair vẫn dùng alias, fallback bị chặn cho step chưa xác minh được; chart vì vậy không được tạo.

Đã chứng minh offline trên execution ghi lại: alias trần → UnsupportedClaim; ID hàng đúng → VerifiedInsight qua các check. Sau đó sửa tổng quát, **không cho provenance nhận alias trần**:

- Catalog thêm `evidence_metric_ids` theo từng step, lấy từ đúng các giá trị model được thấy.
- Query IDs giữ `row[index].column`; statistical IDs giữ tên gốc của deterministic estimates/statistics.
- Prompt nói rõ contract alias chỉ là metadata; `left_metric`, `right_metric`, `evidence_metrics` chỉ chọn trong danh sách evidence IDs.
- Khi bounded prompt bỏ values thì bỏ cả danh sách IDs tương ứng; không cho model chọn evidence không được hiển thị.
- Chỉ sửa `orchestration/evidence_catalog.py`, `orchestration/prompts.py`, thêm test độc lập; không nới provenance/fallback.
- Regression synthetic kiểm tra hai hàng chung alias, thống kê có ID khác contract alias, và alias không xác định phải bị từ chối.

## 5. Bản đồ file cần đọc

Đường dẫn bên dưới tương đối với workspace; nội dung trên disk là nguồn chính xác nếu có khác biệt với bản tóm tắt.

| Nhóm | File |
|---|---|
| Contract/domain | `src/tabular_analytics_agent/domain/models.py`, `domain/__init__.py` |
| Normalization/profile | `src/tabular_analytics_agent/data/normalization.py`, `data/core.py` |
| Overview | `src/tabular_analytics_agent/application/overview.py` |
| Generation schema | `src/tabular_analytics_agent/model_gateway/schemas.py` |
| Binding/flow | `src/tabular_analytics_agent/orchestration/binding.py`, `graph.py` |
| Model evidence/prompt | `src/tabular_analytics_agent/orchestration/evidence_catalog.py`, `prompts.py` |
| Semantic verifier | `src/tabular_analytics_agent/verification/semantic.py`, `verification/__init__.py` |
| Strict evidence resolution, đọc khi chẩn đoán | `src/tabular_analytics_agent/verification/insights.py`, `provenance.py` |
| Full-data report | `docs/implementation-acceptance.md` |
| Semantic report mới nhất | `docs/semantic-acceptance.md` |
| Review ban đầu, chỉ để tham khảo lịch sử | `docs/review-summary.md`, `review-spec-compliance.md`, `review-overfit-architecture.md`, `review-evaluation-release.md`, `review-plan.md` |

Một số review document cũ có phạm vi read-only/cấm API của đợt review ban đầu. Không nhầm các giới hạn lịch sử đó với quyền triển khai/gọi Gemini được người dùng chấp thuận ở các lượt sau.

Test mới chưa track ở thời điểm bàn giao:

- `tests/test_semantic_normalization.py`
- `tests/test_semantic_regression.py`
- `tests/test_metric_contract_validation.py`
- `tests/test_evidence_metric_identifiers.py`
- `tests/fixtures/semantic_regression/dedup_measurements.csv`
- `tests/fixtures/semantic_regression/parcel_measurements.csv`
- `tests/fixtures/semantic_regression/parcel_measurements_invalid.csv`

Unit tests hiện có đã cập nhật payload/contract ở application, evaluation runner, exports, full-data acceptance, hardening, orchestration, semantic grounding, session management, settings và Streamlit workflows. Test PII được đổi từ so khớp thứ tự khóa JSON sang parse metadata, kiểm tra email field có `sample_values=[]` và toàn prompt không lộ email.

## 6. Kết quả kiểm chứng chính xác mới nhất

### 6.1 Offline

Chạy trên successor freeze có sửa evidence ID:

| Check | Kết quả |
|---|---|
| Ruff check toàn repo | PASS |
| Ruff format check | PASS, 107 files |
| Mypy không đối số | PASS, 91 source files |
| Pip check | PASS |
| Pytest exact CI | 615 passed, 2 skipped |
| Coverage với cấu hình branch coverage | 90,70%; gate 90% đạt |

Không dùng coverage từ subset test để kết luận gate toàn repo. Chỉ một agent chạy full coverage tại một thời điểm.

### 6.2 Live mới nhất — đã chạy đủ, không còn bị chặn quota

- Model: `gemini-3.5-flash-lite`.
- Runtime: 8 key được cấu hình, pacing 12 rpm mỗi key; không ghi giá trị key.
- Release: `.eval/20260917T102502Z/results.jsonl` và `summary.json` — **120 lượt, 92 đạt, 0 provider errors, 0 provider retries**.
- Holdout: `.eval/20260917T110108Z/results.jsonl` và `summary.json` — **30 lượt, 27 đạt, 0 provider errors, 0 provider retries**.
- Cả hai chạy cùng source freeze `a8e0d0b449c2`, không sửa source/expected giữa hai suite.

| Quality gate | Ngưỡng | Release | Holdout |
|---|---|---|---|
| Calculation accuracy | ≥95% | 152/177 = 85,88% **FAIL** | 58/66 = 87,88% **FAIL** |
| Schema grounding | 100% | 153/153 PASS | 42/42 PASS |
| Unsupported claim rate | ≤2% | 0/188 PASS | 0/63 PASS |
| Tool execution success | ≥95% | 56/71 = 78,87% **FAIL** | 20/21 = 95,24% PASS |
| Chart validity | ≥95% | 44/60 = 73,33% **FAIL** | 18/18 PASS |
| Evidence completeness | 100% | 188/188 PASS | 63/63 PASS |
| Clarification recall | ≥90% | 30/30 PASS | 6/6 PASS |
| End-to-end success | ≥85% | 92/120 = 76,67% **FAIL** | 27/30 = 90% PASS |

`outcome_accuracy` là số riêng của grader, không đồng nghĩa toàn bộ case đạt. Khi báo cáo dùng rõ pass count và từng gate, không thay bằng một chỉ số cao hơn.

**Kết luận:** đã hoàn tất lượt chạy/thu thập kết quả của phần 4, nhưng chưa đáp ứng điều kiện nghiệm thu hoặc sẵn sàng phát hành. Không quy các thất bại mới nhất cho quota: provider hoàn toàn không lỗi trong hai lượt này.

## 7. Vấn đề còn lại và mức chắc chắn

28 failed records của release mới nhất được phân loại theo log:

| Nhóm lỗi | Số lượt | Điều log chứng minh |
|---|---|---|
| Plan-schema validation | 7 | Payload kế hoạch không hợp lệ sau một lần repair: non_metric/statistics, dedup source fields, numerator fields sai operation |
| Metric-contract rejection | 7 | Verifier từ chối 6 rate expressions và 1 transform |
| Completed nhưng thiếu insight yêu cầu | 6 | Session hoàn tất nhưng thiếu nội dung deterministic mà grader yêu cầu |
| Repeated Tool Action | 5 | Repair lặp cùng action và hết bounded budget |
| Conversion/normalization | 3 | Chuyển đổi text số/ngày thất bại trong execution |

**Không suy diễn quá mức từ các nhãn này:**

- Verifier rejection không tự chứng minh SQL sai về toán học. Có thể contract quá hẹp so với công thức người dùng cần, hoặc model tạo SQL sai. Phải đọc request → plan → SQL → deterministic result → verification để phân biệt.
- Đặc biệt rà khả năng biểu diễn rate/ratio khi contract chỉ cho `count`/`sum` mỗi vế; đây là điểm cần chẩn đoán, chưa phải kết luận hay quyết định mở schema tùy ý.
- Không chữa bằng cách nhận alias không rõ hàng, bỏ fail-closed, chấp nhận TRY_CAST làm mất dòng, tăng repair vô hạn, hardcode đáp án hay sửa expected.
- Chart là đầu ra downstream: tìm vì sao calculation/insight không hoàn tất trước khi sửa renderer.
- Holdout chỉ dùng để báo chất lượng. Không dùng case lỗi holdout làm nguồn tune bản tiếp theo.

## 8. Artifacts và lịch sử freeze — tránh đọc nhầm kết quả

| Artifact | Vai trò |
|---|---|
| `.eval/semantic-baseline-20260916.json` | 61 hash: 40 release cases + 10 holdout cases + 11 fixture liên quan; cuối run 0 changed/0 missing |
| `.eval/full-data-final-audit-20260916.json` | Audit của vòng full-data trước, có snapshot rộng hơn được report tham chiếu |
| `.eval/full-data-freeze-20260916.json` | Freeze full-data lịch sử |
| `.eval/semantic-freeze-20260917.json` | Freeze semantic trước sửa alias, ID `4fe97433f7d2`, 176 files |
| `.eval/semantic-freeze-20260917-contractfix.json` | **Freeze source hiện tại đã nghiệm thu live**, 177 files |
| `.eval/20260917T102502Z/` | **Release hiện tại**, 92/120, không lỗi provider |
| `.eval/20260917T110108Z/` | **Holdout hiện tại**, 27/30, không lỗi provider |
| `.eval/release-semantic-4fe97433f7d2-network/` | Lượt trước sửa alias: 48/120, 13 rate-limit errors; không phải score hiện tại |
| `.eval/holdout-semantic-4fe97433f7d2-network/` | Holdout cũ chỉ 3 records, không có completed score |
| `.eval/release-semantic-a8e0d0b449c2-network/` | Attempt mới sau sửa alias từng bị 3 rate-limit errors, không phải lượt hoàn chỉnh mới nhất |
| `.eval/gemini-diagnostic-a8e0d0b449c2.json` | Diagnostic đã làm sạch: HTTP 429, retry 59s, không xác định phút/ngày |

SHA256 aggregate của freeze hiện tại:

```text
a8e0d0b449c2e9e94e2168f241a970a3f8ec6b660752295d24238e4428d1d1d9
```

Kiểm tra cuối: source/test/config/evaluation cases/fixtures không đổi; `docs/semantic-acceptance.md` có drift được ghi nhận vì cập nhật kết quả **sau** freeze. File bàn giao này cũng được tạo sau freeze, không thuộc 177 file đã khóa. Không cập nhật đè manifest cũ để che thay đổi.

`.eval` có thể là dữ liệu local/ignored. Nếu chuyển sang workspace/máy khác, cần mang theo source untracked, fixtures mới, báo cáo và các manifest/results nêu trên. Không chỉ gửi commit HEAD; cũng không gửi `.env` hoặc credentials kèm handoff.

## 9. Đề xuất trình tự Claude tiếp tục

1. Đọc file này, `docs/semantic-acceptance.md`, `docs/implementation-acceptance.md`; kiểm tra `git status`, HEAD và file untracked. Không áp dụng lại những sửa đổi đã hoàn thành.
2. Kiểm tra AGENTS hiện hành. Repo yêu cầu ưu tiên MCP knowledge graph cho code discovery; index từng cũ nên xác nhận actual source, fallback khi graph thiếu/sai. Tìm string/config bằng `rg`.
3. Chẩn đoán **release-only** theo từng nhóm lỗi ở mục 7. Phân biệt lỗi model/prompt, schema không biểu diễn đúng yêu cầu, verification quá hẹp và normalization execution sai. Đừng mặc định cần thêm một tầng abstraction mới.
4. Với mỗi nguyên nhân chung đã chứng minh, tạo regression synthetic độc lập thể hiện đúng phép tính và failure boundary; giữ baseline bất biến. Sửa tối thiểu tổng quát, không vá theo tên case hoặc giá trị expected.
5. Giữ contract full-data, provenance và cleanup; không cứu pass rate bằng sampling/partial result hoặc bỏ verification.
6. Chạy focused tests với `--no-cov`, rồi exact full CI khi source ổn định. Nếu test phát hiện vấn đề mới, xử lý và rerun phần cần thiết; không chạy lại liên tục chỉ để tìm một lượt xanh.
7. Khi offline green, tạo **manifest freeze mới**, không ghi đè `a8e0d0b449c2`. Xác nhận baseline 61 file không đổi; giữ source không đổi trong release/holdout.
8. Chạy live theo quyền đã có và nhịp 12 rpm, output mới. Không launch runner thứ hai khi runner cũ còn chạy; không tiếp tục ghi thêm vào partial artifact như thể một suite hoàn chỉnh.
9. Báo số liệu thật, các gate chưa đạt và rủi ro còn lại. Không tuyên bố dự án hoàn tất vì unit tests xanh hoặc holdout E2E vượt ngưỡng trong khi calculation gate vẫn fail.

Ưu tiên hiện tại là xử lý các nguyên nhân semantic tổng quát còn lại, không bắt đầu Docker/UI hoặc refactor toàn hệ thống để né các gate đang đỏ.

## 10. Lệnh tham khảo

Chạy từ workspace bằng PowerShell. Các lệnh API dưới đây tiêu tốn quota/chi phí Gemini và cần quyền mạng phù hợp; quyền gửi evaluation payload đã được người dùng chấp thuận trong chat.

```powershell
git status --short
git branch --show-current
git log -1 --format="%H %s"

.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m mypy
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
.\.venv\Scripts\python.exe -m pip check
```

Không thêm `--no-cov` vào full CI. Chỉ dùng cho focused runs; tránh các pytest process cùng ghi `.coverage` hay dùng chung temp-root đang bị khóa trên Windows.

Ví dụ live cho một **bản mới đã freeze và offline green**:

```powershell
.\.venv\Scripts\python.exe -m tabular_analytics_agent.evaluation.runner --cases tests/evaluation_cases_release --runs 3 --rpm 12 --output .eval/release-NEW-FREEZE-ID
.\.venv\Scripts\python.exe -m tabular_analytics_agent.evaluation.runner --cases tests/evaluation_cases_final --runs 3 --rpm 12 --output .eval/holdout-NEW-FREEZE-ID
```

Thay `NEW-FREEZE-ID` bằng ID mới, không copy đè output cũ. Chạy tuần tự, không đổi source giữa hai suite. Không đoán giá token hoặc báo cost khi chưa có metadata giá chính xác.

**Điểm bàn giao cuối:** triển khai hiện tại và regression đã lưu đầy đủ; latest live hoàn tất, không lỗi provider; dự án vẫn chưa đạt quality gates. Việc cần tiếp tục là sửa các thiếu sót semantic còn lại bằng nguyên nhân chung và kiểm chứng độc lập, không phải chờ quota hoặc chạy lại y nguyên để lấy điểm đẹp hơn.
