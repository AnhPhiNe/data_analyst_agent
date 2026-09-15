# Rà soát dự án so với Spec.md — 2026-09-15

## Kết luận

**Chưa nên tuyên bố MVP đã hoàn tất.** Kiến trúc chính phù hợp định hướng local-first, một Orchestrator, typed tools và deterministic verification. Tuy nhiên, có lỗi tích hợp xác nhận được và bằng chứng nghiệm thu phát hành chưa đầy đủ.

**Không đề xuất viết lại dự án.** Chưa có bằng chứng đủ mạnh để kết luận overfitting ở cấp hệ thống. Complexity lớn của contracts, checkpoint, verification, field IDs và quota handling được Spec yêu cầu. Cần sửa hợp đồng giữa các thành phần trước, sau đó mới cân nhắc refactor nhỏ.

## Phạm vi và phương pháp

- Toàn bộ workspace tại HEAD `c6f703f2c7a06d1430aa6b3258c7fbeeef53e8d9`, đối chiếu Spec v1.4, không chỉ git diff.
- Các nhánh Luna Max thực hiện rà soát; điều phối kiểm tra lại nguồn, tái hiện lỗi chính và tổng hợp ưu tiên.
- Knowledge graph không đầy đủ: 29 file Python trong index so với 49 file Python hiện có trong `src`. Các phát hiện được đối chiếu với file thực tế.
- Không sửa source/fixture/holdout, không gọi live model, không đọc secrets. Tệp `docs/RESUME-HANDOFF.md` có sẵn được giữ nguyên.

## Phát hiện đã kiểm tra chéo

### R1 — P1: chart thất bại sau khi xác nhận semantic annotations

**Điều khoản:** FR-04, FR-11, Verification Gates §13.

`orchestration/graph.py:1106` tạo source reference với annotations hiện tại. Nhưng `visualization/renderer.py:76` tạo expected reference với annotations rỗng, rồi so sánh cả fingerprint ở dòng 97.

**Tái hiện độc lập:** dùng cùng QueryResult, ToolAction đã verified và table intent trong fixture; không có annotation thì tất cả gate pass. Khi thêm một annotation xác nhận `revenue = Net revenue`, chỉ gate `source_result_binding` fail.

**Sửa tối thiểu:** truyền semantic context hiện tại từ nguồn đáng tin cậy vào renderer/validator. Không bỏ gate, không lấy expected fingerprint từ chính intent đang được kiểm tra. Chưa cần migration fingerprint để sửa lỗi này.

### R2 — P1: điều kiện lọc trong CTE/subquery mất khỏi evidence metadata

**Điều khoản:** FR-10, FR-13, §13.3 và §25.1.

`data/sql_policy.py:215` chỉ lấy WHERE/HAVING của SELECT ngoài cùng. Với `SELECT SUM(revenue) ... WHERE region = 'North'`, filters ghi nhận điều kiện; đưa cùng điều kiện vào CTE thì filters trở thành `()`.

SQL gốc vẫn tồn tại để tái lập, nhưng metadata/claim có thể thiếu phạm vi lọc. Vì vậy đây là lỗi completeness của scope, không phải mất toàn bộ khả năng tái lập.

**Sửa tối thiểu:** lưu điều kiện kèm phạm vi truy vấn của nó. Không nối mọi predicate bên trong thành một AND ở ngoài: điều đó có thể diễn giải sai JOIN, EXISTS hoặc nhánh CTE. Kiểm tra cả WHERE, HAVING, nested query và cách claim/export trình bày phạm vi.

### R3 — P1: ngoại lệ COUNT(*) không thống nhất giữa các tầng

**Điều khoản:** §25.4 cho phép row count không có metric mapping và không cần source field.

`orchestration/binding.py:55-65` từ chối SQL step có `required_fields=()`. `domain/models.py:350` cũng từ chối EvidenceTrail không có source field. Quy tắc chung ở §15 cần được làm rõ theo ngoại lệ cụ thể ở §25.4.

**Giới hạn kết luận:** câu hỏi đếm dòng đơn giản vẫn có thể được trả lời trực tiếp từ Data Profile. Lỗi nằm ở hợp đồng Tool Action SQL COUNT(*) không đọc cột.

**Sửa tối thiểu:** biểu diễn và kiểm chứng row count ở mức dataset; giữ identity/hash/version, SQL và row counts. Chỉ cho phép không có source field khi AST thực sự không đọc field, không nới validation cho mọi query.

### R4 — P2: KPI có thể làm tròn trái yêu cầu hiển thị đầy đủ chữ số

**Điều khoản:** FR-11.

`visualization/renderer.py:340` đặt định dạng mặc định `,.12~g`, giới hạn 12 chữ số có nghĩa. Đây là sửa chữa chưa đủ tổng quát so với yêu cầu mọi significant digit. Cần kiểm tra số lớn, số thập phân, giá trị âm và định dạng model yêu cầu; không chỉ kiểm một số bốn chữ số.

### R5 — P2: qualified field ID là điểm yếu thực thi đã quan sát

`data/sql_policy.py:65` bỏ qua cột có qualifier, ví dụ `d.c5`. Báo cáo v9 ghi nhận một query thất bại ở nhánh này. Đây là giới hạn reliability; §25.1 hiện chỉ cam kết rewrite **unqualified** IDs, nên không xếp thành vi phạm trực tiếp điều khoản đó.

Sửa bằng SQL scope/alias binding hoặc từ chối rõ để repair về dạng hỗ trợ. Không replace chuỗi `d.c5` toàn cục, không rewrite alias tính toán hay tên field thật trùng ID.

### R6 — P1 nghiệm thu: grader chưa đo đủ các quality gate

`evaluation/runner.py:127-170` chấm outcome, forbidden substrings và nhóm answer/profile checks. `runner.py:226-250` tổng hợp các boolean checks theo case-run. `allowed_filters` và `supported_conclusions` trong case chưa trở thành kiểm tra đầy đủ; không có gate duyệt evidence của **mọi** insight, mức cạn repair budget hay clarification recall với denominator riêng.

Nhánh đánh giá tái hiện state có thêm insight thiếu evidence hoặc failed action sau output đúng mà grader vẫn pass. Đây là bằng chứng **grader không kiểm tra gate**, không phải bằng chứng runtime Pydantic có thể xuất bản insight không hợp lệ qua đường đi bình thường.

Số liệu v9 lịch sử: 20/36 run pass; calculation-check 29/33; schema-check 32/33; chart-check 18/30. Các tỷ lệ này không tự thay thế các denominator của §17.4: đặc biệt calculation-check theo case-run khác tỷ lệ từng expected value, và forbidden-substring pass khác tỷ lệ Unsupported Claims trên mọi Verified Insight.

**Sửa trước release:** bổ sung bảng riêng cho từng gate với tử số/mẫu số, positive/negative cases, và semantic review rubric. Bộ release cần ít nhất **40 case** bao phủ bốn dataset tiers, trên một mốc code/prompt/grader cố định. Development/contaminated cases chỉ báo cáo hồi quy riêng, không trộn vào clean-holdout gate. Giữ nguyên v9; kỳ vọng chart sửa phải nằm ở bản mới có version và số liệu tái chấm riêng nếu có.

### R7 — P2: lifecycle dismiss và assumption reporting

- `streamlit_app.py:529-531` chỉ xóa agent state trong bộ nhớ khi dismiss clarification; không cập nhật checkpoint bền vững. Câu hỏi mới có thể thay thế run cũ, nên đây không phải deadlock. Cần hủy run có checkpoint hoặc ghi rõ nút chỉ ẩn tạm thời, và kiểm tra reopen/reload.
- `statistics/engine.py:199-215` và `:276-292` không truyền assumption checks cho Mann–Whitney/Kruskal–Wallis; nhánh chức năng tái hiện `assumptions=()`. Bổ sung các kiểm tra áp dụng được và cảnh báo/NOT_CHECKED cho điều kiện không thể suy ra từ bảng. Không tự nhận rằng independence đã được chứng minh.

## Overfitting và overengineering

### Chưa đủ bằng chứng overfitting

- v7 đạt 36/36, v8 đạt 24/36 và v9 đạt 20/36 là các bộ câu hỏi/dữ liệu và mốc triển khai khác nhau. Điểm giảm chưa tự chứng minh overfit.
- Việc ghi nhận holdout trở thành development evidence sau khi dùng để sửa là đúng hướng.
- Bốn run v9 bị đánh trượt chart vì kỳ vọng loại KPI không phù hợp đã được tài liệu thừa nhận; không được dùng chúng như bằng chứng bốn phép tính sai.
- Pooled aggregate và ranking mơ hồ vẫn là khoảng trống hiểu ý định. Cần đo bằng paraphrase/dữ liệu chưa thấy và đánh giá semantic scope, tránh thêm danh sách từ khóa theo câu holdout.

### Refactor có ích nhưng không cần làm lớn

1. Hai fingerprint helper tại `domain/models.py:425` và `verification/insights.py:39` có thứ tự canonicalization khác nhau. Đây là rủi ro bảo trì, chưa chứng minh lỗi độc lập vì caller hiện dùng từng nhóm nhất quán. Hợp nhất riêng, có chính sách tương thích dữ liệu cũ.
2. `model_gateway/schemas.py:150` hard-cap plan ở 12 bước trong khi ExecutionBudget có giới hạn cấu hình. Cần một effective limit rõ ràng xuyên suốt.
3. Chính sách inferential operation/alpha bị lặp giữa binding và statistics. Gom policy nhỏ nếu giúp tránh cập nhật sót.
4. `graph.py` lớn không tự chứng minh overengineering. Tách prompt builder/node chỉ khi có lợi ích bảo trì cụ thể; giữ node names, checkpoint/state schema và public API.

**Giữ nguyên:** một Orchestrator, typed contracts, deterministic tools/claims, verification gates, checkpoint, FakeModelGateway, field IDs, ngân sách thực thi và bảo vệ PII. Không thêm multi-agent runtime, provider fallback, framework plugin hoặc chuyển React/FastAPI để giải quyết các lỗi trên.

## Kế hoạch sửa theo dependency

| Đợt | Công việc | Điều kiện hoàn thành |
|---|---|---|
| 1 — Tính đúng đắn | R1 chart semantic binding; R2 filter scope; R3 COUNT contract; R4 KPI precision; assumption reporting R7 | Regression tests theo lớp hành vi, gồm negative cases; không bỏ safety gates; query/evidence/chart/export nhất quán |
| 2 — Reliability | R5 scope-aware IDs; lifecycle dismiss bền vững nếu xác nhận; đo pooled/grouped và ambiguous ranking | Các query alias/CTE/paraphrase mới được xử lý đúng hoặc làm rõ; không vá riêng câu v9 |
| 3 — Refactor nhỏ | Effective plan limit; chia sẻ policy thống kê; fingerprint compatibility riêng | Hành vi cũ giữ nguyên, không migration âm thầm, kiểm checkpoint/export/artifact round-trip |
| 4 — Nghiệm thu | R6: grader, release suite cố định đủ §17.3 và tất cả gate §17.4; kiểm UI và Docker; đồng bộ CI/README/checklist | Báo cáo có từng numerator/denominator, phiên bản mã/prompt/grader/dataset; clean setup và Docker có bằng chứng thực chạy |
| Hoãn | Tách graph diện rộng, HTML/PDF/notebook, provider mới, thay UI | Chỉ làm khi Must và quality gates đạt; không dùng mở rộng scope để che reliability gap |

Mỗi thay đổi về prompt/model-facing behavior phải ghi version và được đo lại. Giữ nguyên lịch sử holdout; nếu cần sửa grader hoặc expectation, báo cáo riêng lý do và số liệu, không ghi đè kết quả ban đầu.

## Ghi chú nghiệm thu

README và checklist đang trộn implementation coverage với acceptance. `docs/mvp-acceptance.md` mở đầu bằng v1.1 dù Spec hiện là v1.4. Bộ CI `.github/workflows/quality.yml:17` không dùng constraints trong lệnh cài đặt; Docker có check target nhưng chưa có bằng chứng build được trong tài liệu và CI chưa chạy nó.

Ảnh, demo video và case studies được tài liệu ghi nhận là đã hoãn theo quyết định người dùng. Chúng không được tự đưa trở lại thành Must blocker trong báo cáo này.

## Báo cáo nguồn

### Kiểm chứng offline ở mốc rà soát

Điều phối chạy lại trên workspace hiện tại:

- `python -m pytest -q -p no:cacheprovider`: **370 passed, 2 skipped**, 73,82 giây; coverage nhánh **90,95%**, vượt ngưỡng 90%.
- `python -m ruff check .`: pass.
- `python -m ruff format --check .`: **87 file** đạt định dạng.
- `python -m mypy`: pass, **73 source files**.
- Tái hiện độc lập lỗi chart semantic binding và mất filters trong CTE như R1/R2.

Không chạy live Gemini, manual browser acceptance hoặc Docker trong đợt này. Test xanh nhưng R1/R2 vẫn tái hiện được là bằng chứng cần bổ sung test tích hợp giữa các thành phần, không chỉ tăng phần trăm coverage.

### Tài liệu chi tiết

- `review-plan.md`: phân công và quy trình.
- `review-spec-compliance.md`: ma trận FR và phát hiện chức năng.
- `review-overfit-architecture.md`: phân tích kiến trúc, prompt và refactor.
- `review-evaluation-release.md`: kiểm thử, grader và release gates.

Ưu tiên và phạm vi sửa trong báo cáo tổng hợp này là kết quả điều phối kiểm tra chéo; chúng có thể hẹp hơn đề xuất ban đầu của từng nhánh.
