# Audit đánh giá và bằng chứng phát hành

Ngày audit: 2026-09-15

Phạm vi: kiểm tra tính hợp lệ của bộ đánh giá, nguy cơ nhiễm holdout và overfit, sai số của grader, chất lượng test, các cổng phát hành, Docker, CI và khả năng tái lập. Audit đối chiếu 'Spec.md', 'README.md', 'docs/mvp-acceptance.md', 'docs/limitations.md' với runner, model, case JSON, test và workflow thực tế.

Kết luận phát hành: **HOLD**. Mã nguồn hiện vượt qua các kiểm tra tĩnh và test tự động offline, nhưng bằng chứng chưa đủ để tuyên bố mọi cổng §17.4 đã đạt. Kết quả v9 giảm còn 20/36 run pass; nhiều cổng §17.4 chưa được grader triển khai; bộ hồ sơ chưa khóa manifest gồm case, tier, commit, grader version, model/provider và raw trace. Vì vậy kết quả v7 '36/36' chỉ là kết quả của một phiên bản suite lịch sử, chưa phải bằng chứng hồi quy trên một bộ frozen dùng chung.

Không có source edit, không đọc secret hoặc '.env', không cài dependency mới và không gọi Gemini thật. 'docs/RESUME-HANDOFF.md' (và các file untracked có trước audit) được giữ nguyên. Quyết định của user về việc hoãn portfolio case studies, screenshots và video được tôn trọng; audit này không coi các artifact đó là Must bị thiếu.

## 1. Phương pháp và kết quả kiểm tra offline

Knowledge graph của project chỉ phản ánh khoảng 36 file và thiếu phần lớn evaluator/evaluation artifacts, nên chỉ dùng để định hướng cấu trúc; các kết luận dưới đây được đối chiếu bằng source, JSON cases, docs, git history và các file '.eval' hiện có. '.eval' đang bị ignore và không phải release artifact đã commit.

Các lệnh đã chạy trong virtualenv hiện có:

- '.\.venv\Scripts\python.exe -m ruff check .' — **PASS**, 'All checks passed!'
- '.\.venv\Scripts\python.exe -m ruff format --check .' — **PASS**, '87 files already formatted'
- '.\.venv\Scripts\python.exe -m mypy' — **PASS**, 'Success: no issues found in 73 source files'
- '.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider' — **PASS**, '370 passed, 2 skipped in 71.25s', branch coverage '90.95%' (ngưỡng cấu hình 90% trong 'pyproject.toml:45-56')
- 'Get-Command docker' — Docker chưa được cài, vì vậy không thể xác nhận image build hoặc target 'check' trên máy audit.

Các kết quả trên xác nhận health của test/lint/type hiện tại. Chúng không xác nhận live-provider behavior, latency/cost, Docker runtime, hoặc các cổng §17.4 mà runner không tính.

## 2. Claims đã phát hành và evidence thực tế

| Claim trong hồ sơ | Evidence đã kiểm tra | Đánh giá |
|---|---|---|
| README nói MVP đã triển khai và đã đạt 11/14 acceptance criteria ('README.md:12-18') | 'docs/mvp-acceptance.md:36-71' vẫn liệt kê manual flow, live evaluation, semantic/filter review, hardening và Docker là công việc/evidence cần hoàn thiện; criteria §21.11-§21.14 yêu cầu eval, clean environment, không secret/real dataset và limitations evidence | Có một phần evidence, nhưng claim 11/14 không đủ để suy ra release-ready |
| Bộ v3-v8 được mô tả là 97 committed cases và v7 đạt 36/36 ('docs/mvp-acceptance.md:36-71,181-192') | Inventory hiện tại là 109 JSON cases: development 10, clean holdout 15, contaminated 2, v3 10, v4-v9 mỗi set 12. v9 chỉ chạy 12 case × 3 = 36 run | 97 là snapshot trước v9, không phải một frozen release inventory; v7 là historical measurement |
| v9 là measurement mới nhất ('Spec.md:748-760', 'docs/mvp-acceptance.md:213-237') | Raw local summary cho thấy 20/36 run pass, outcome 32/36, calculations 29/33, insight 29/33, schema 32/33, chart 18/30, forbidden 36/36 | Đây là evidence hồi quy hiện tại và không đạt ngưỡng E2E 85% |
| Các rate trong summary là đủ để kết luận gates | 'runner.py:226-250' chỉ tổng hợp các check mà runner biết; không có denominator/tier/version manifest và loại provider-final-error khỏi check-rate | Các rate có thể đọc được, nhưng không phải bảng §17.4 đầy đủ |
| Docker là đường cài đặt có constraint ('Dockerfile:19-34') | Workflow chỉ chạy 'python -m pip install -e .[dev]' ('.github/workflows/quality.yml:1-25'), không truyền '--constraint constraints.txt'; không có Docker build job | Hai môi trường cài dependency khác nhau; reproducibility của CI chưa tương đương Docker |

README đã ghi rõ Docker image chưa được build ('README.md:12-18'). Đây là evidence đúng về limitation, nhưng không thể được diễn giải thành Docker đã qua release gate.

## 3. Đánh giá từng cổng §17.4 và §21

'Spec.md:576-589' yêu cầu các cổng expected values, schema grounding, unsupported claim, tool execution, chart, evidence completeness, clarification recall và E2E; latency/API cost phải được báo cáo. 'Spec.md:662-680' đưa các yêu cầu đó vào acceptance criteria, cùng clean environment, không secret/real dataset và limitations.

| Gate | Kết quả/evidence | Trạng thái |
|---|---|---|
| Expected values ≥95% answered cases | v9 có calculation check ở mức **29/33 case-runs = 87.9%**, nhưng đây không phải accuracy theo từng expected value của §17.4; runner chưa tính tử số/mẫu số expected-value accuracy. Historical v7 hiển thị 100% calculation check | **UNVERIFIED** cho đúng gate §17.4; v7 không thay thế được v9 |
| Schema fields 100% cho successful Tool Actions | v9: 32/33 = 97.0%; runner kiểm tra field xuất hiện trong successful action/result ('runner.py:346-387') | **FAIL**; phép đo chỉ là grounding bề mặt |
| Unsupported claim ≤2% | Có 'forbidden_claims' 36/36, nhưng 'supported_conclusions' được nạp trong model ('models.py:76-90') và không được dùng để kiểm tra semantic conclusion trong grader | **UNVERIFIED**, không được gọi là PASS |
| Tool execution ≥95%, không cạn repair budget | Runner không kiểm tra số failed actions, 'repair_count' hay budget exhaustion. 'run_case' chỉ tự approve tối đa 3 lần ('runner.py:102-124') | **UNVERIFIED** |
| Chart ≥95% valid type | v9: 18/30 = 60.0% | **FAIL**, cần tách lỗi contract khỏi lỗi agent |
| Evidence completeness 100% | Runner chỉ kiểm tra một assertion có thể cover expected calculation ('runner.py:565-615'), không duyệt mọi 'Verified Insight' để bắt buộc có Evidence Trail | **UNVERIFIED** |
| Clarification recall ≥90% và unnecessary clarification | Case có yêu cầu clarification tồn tại, nhưng 'runner.py:346-387' không tính recall/precision theo denominator clarification | **UNVERIFIED** |
| E2E ≥85% mọi check | v9 20/36 = 55.6% run pass; historical v7 36/36 | **FAIL hiện tại** |
| Latency và API cost | Runner có average 'latency_ms' trong summary ('runner.py:226-250') và lưu token/call trace ('runner.py:127-170'), nhưng cost và bảng gắn với frozen suite chưa có trong docs | **PARTIAL** |
| Clean environment, tests/lint/types | Offline test/lint/type pass; Docker chưa thể chạy; CI dependency path không dùng constraints | **PARTIAL** |
| Không secret/real dataset | '.gitignore:11-25' loại '.env'/data; '.env.example' chỉ placeholder; 'git ls-files .env' chỉ thấy example | **PASS cho repo scan**, chưa phải kiểm tra mọi môi trường triển khai |
| Limitations | 'docs/limitations.md:6-37,99-107,150-185' mô tả model variability, synthetic data và v8/v9 failures | **PASS**, nên gắn cùng release manifest |

### Phân biệt lỗi grader/case với lỗi agent trong v9

Không nên gộp cả sáu chart-only failures thành sáu defect của agent. 'docs/mvp-acceptance.md:222-237' ghi rõ bốn failure đến từ expected type của case loại KPI không khớp với output KPI hợp lệ; hai failure còn lại là chart invalid hoặc tiếng Việt bị garbled. Bốn lỗi đầu là **false negative của evaluation contract** cần sửa case schema hoặc quy ước valid chart types; hai lỗi sau mới là ứng viên lỗi đầu ra cần triage riêng. Các failure pooled-per-industry và top-channels pooled được ghi nhận độc lập vì đó là sai khác semantics của kết quả, không phải chỉ lỗi chart annotation.

## 4. Các lỗ hổng false positive/false negative đã tái hiện

### False positive 1: allowed_filters không được chấm

'GoldenCase' có trường 'allowed_filters' ('models.py:76-90'), và case 'tests/evaluation_cases_holdout/ship_units_shipped_in_march.json:25-27' yêu cầu filter 'ship_date in March 2026'. 'runner.py:346-387' chỉ chấm calculations, coverage, schema và chart; không kiểm tra filter provenance hay predicate thực tế. Repro offline: tạo 'GradingState' có expected value 1688 và result hợp lệ nhưng không có filter metadata; gọi 'grade_run' vẫn trả 'passed=True'. Đây là pass giả cho câu trả lời có thể đạt đúng số bằng cách dùng toàn bộ bảng.

### False positive 2: thiếu Evidence Trail vẫn pass

'runner.py:565-615' chỉ tìm một assertion/evidence cover required calculation; không yêu cầu mọi insight được gắn evidence. Repro offline: giữ assertion đúng cho expected values, thêm một 'Verified Insight' không có 'evidence'; 'grade_run' vẫn pass với các check hiện có. Vì §17.4 yêu cầu evidence completeness 100%, gate này chưa được thực thi.

### False positive 3: failed Tool Action/repair exhaustion không bị tính

'runner.py:127-170' lưu calls và tokens nhưng 'grade_run' không fail khi trace có thêm action thất bại; 'run_suite' ('runner.py:173-223') chỉ retry provider-final-error. Repro offline: thêm failed action với 'retry_count=99' và lỗi 'Tool repair budget exceeded' sau một successful action đúng; 'grade_run' vẫn pass. Do đó không thể suy ra tool execution gate từ E2E pass rate.

### False positive 4: unsupported semantic conclusion và metric alias quá rộng

'supported_conclusions' có trong case model nhưng không có check tương ứng. Ngoài ra '_metric_matches' ('runner.py:531-562') bỏ qua tên metric trong một số grouped/single-row cases để chấp nhận alias; test 'tests/test_evaluation_runner.py:337-348' còn chủ ý cho phép single-row value dưới bất kỳ metric alias nào. Điều này làm giảm false negative do cách diễn đạt của model, nhưng mở ra false positive: đúng scalar nhưng sai metric. Cần một lớp semantic check có allowlist rõ ràng thay vì coi mọi alias là tương đương.

### False negative: expected chart type không bao quát KPI

Bốn v9 chart-only failures nêu tại 'docs/mvp-acceptance.md:222-237' là lỗi contract của case/grader. Nếu sản phẩm cho phép KPI render, 'valid_chart_types' của case phải thể hiện điều đó; nếu không, agent không được render KPI. Phải sửa một trong hai contract trước khi dùng chart rate làm quyết định release. Đây là lý do báo cáo tách “case/grader false negative” khỏi “agent behavior failure”.

### Test realism

'tests/test_streamlit_workflows.py:1-78' dùng synthetic data và fake gateway; 'tests/test_hardening.py:100-313' kiểm tra zip bomb, timeout, memory, injection, PII và recovery. Đây là kiểm tra an toàn có giá trị, nhưng không mô phỏng model/provider drift. 'docs/limitations.md:99-107' cũng xác nhận suite phần lớn synthetic, Iris/Titanic là public, expected values deterministic từ pandas/SciPy. Cần hidden holdout và semantic mutation cases để bắt sai metric, filter, pooled-vs-grouped và evidence thiếu.

## 5. Holdout, contamination và overfit

'tests/test_evaluation_runner.py:94-148' kiểm tra mỗi dataset path được dùng một lần, path không giao với các set trước, case ID duy nhất và fixture SHA. Đây là guard tốt ở mức file/dataset. Nó **không** khóa hash của case JSON, tier, source commit, grader version, model/provider, prompt/config hay kết quả; cũng không buộc lệnh release chỉ chọn clean holdout.

'GoldenCase.require_gradable_expectations' ('models.py:96-108') bắt buộc required calculations, allowed fields và supported conclusions cho answered cases, nhưng không bắt buộc 'allowed_filters', tier hoặc expected clarification contract. Inventory cho thấy 'allowed_filters' chỉ có ở 3/15 clean holdout, 2/10 v3, 1/12 v4 và 0 ở development/contaminated/v5-v9. Vì vậy guard có thể cho suite “gradable” dù filter-sensitive cases hầu như không có provenance expectation. Bốn dataset tiers là trục phân tầng riêng với trạng thái contamination; không được dùng development hoặc contaminated để làm điều kiện bắt buộc của clean release denominator.

Các case v3-v9 nằm trong git history theo từng commit, nhưng raw '.eval' summaries không được track ('git ls-files .eval' trả 0). Không có release manifest liên kết từng run với commit/case hash. Do đó chưa thể xác minh yêu cầu “≥40 cases, đủ mọi tier, được đánh giá cùng một frozen version” nếu đây là ngưỡng release được áp dụng cho §17/§21. Hiện chỉ chứng minh được v9 là 36 run trên 12 case; inventory 109 case không đồng nghĩa 109 case đã chạy cùng version.

Confidence overfit: **thấp đến trung bình** cho claim tổng quát. Lý do:

- v7 đạt 36/36, sau đó v8 còn 24/36 và v9 còn 20/36 ('README.md:56-77', 'docs/limitations.md:19-37');
- mỗi version chỉ có 12 case × 3 run, nên một case all-fail có thể thay đổi khoảng 8.3 điểm phần trăm;
- docs ghi suite chủ yếu synthetic và các failure trước đó được cùng tác giả sửa ('docs/limitations.md:19-37,99-107');
- holdout-clean summary local có 45 run, 38 pass, grade failures 2; các runner check rates là calculation case-run 83.3%, chart 81.0%, insight 80.0%. Đây là tín hiệu generalization thấp hơn v7, nhưng vì '.eval' không tracked nên cần freeze/re-run để dùng làm release evidence;
- các số v3-v9 không nên pool thành một rate duy nhất: grader/rules/cases thay đổi theo version, denominator khác nhau và một số provider-final-error bị loại khỏi check rate.

Đây là rủi ro overfit của measurement protocol và grader contract, không phải kết luận rằng mọi logic sản phẩm đã overengineer. Tuy nhiên các alias/regex permissive, nhiều version suite và auto-approval 3 bước có thể che regression semantic trong khi làm evaluator phức tạp hơn. Ưu tiên làm rõ contract và evidence trước khi thêm heuristic mới.

## 6. Docker, CI và setup release

'Dockerfile:10' dùng 'python:3.12-slim'; 'Dockerfile:19-22' cài package qua 'constraints.txt'; target check ở 'Dockerfile:24-34' chạy Ruff, format, mypy và pytest. Đây là đường kiểm tra có tính tái lập tốt hơn install range trực tiếp.

'.github/workflows/quality.yml:1-25' chỉ có một quality job. Dòng 17 cài '-e .[dev]' mà không dùng '--constraint constraints.txt'; workflow không build Docker image, không chạy Docker target 'check', không chạy evaluator hoặc upload raw evaluation artifacts. Vì Docker chưa cài trên máy audit, trạng thái Docker hiện là **unverified**, không phải fail runtime.

Release reproducibility finding: CI và Docker đang dùng hai dependency resolution paths. Nếu package index thay đổi, CI có thể xanh với dependency khác Docker. Acceptance cần buộc cả hai dùng cùng constraints, ghi Python/package lock metadata và lưu log. Đây là consistency/release evidence issue, chưa phải source behavior defect.

## 7. Trình tự remediation đề xuất

### Bước 1 — Đóng băng hồ sơ đánh giá

Tạo một release manifest bất biến cho một phiên bản duy nhất, gồm: case ID, tier, case JSON SHA256, dataset SHA256, commit SHA, grader/runner version, prompt/config hash, model/provider, số lần lặp, timestamp, raw trace và summary có tử số/mẫu số. Commit hoặc lưu artifact ngoài '.eval' ignored. Acceptance: có thể checkout đúng commit và tái tạo cùng danh sách case, cùng denominator và cùng summary.

### Bước 2 — Xác định suite release và denominator

Chọn ít nhất 40 case unique theo §17.3, phủ bốn dataset tiers được Spec định nghĩa, và chạy tất cả bằng cùng frozen runner/version. Clean release denominator phải chỉ rõ suite/tier clean được chọn; development và contaminated giữ thành các báo cáo hồi quy riêng, không bắt buộc đưa vào clean numerator/denominator. Ghi rõ một “case” và một “run” khác nhau, số repetitions, cases requiring clarification, cases expecting chart, answered cases và provider-error exclusions. Acceptance: bảng denominator cộng được về tổng, không có rate không có tử số/mẫu số; historical v7 được ghi là baseline, không trộn vào release score.

### Bước 3 — Làm grader fail-closed theo §17.4

Bổ sung checks cho: allowed filter/predicate provenance, supported conclusion/semantic claim, mọi Verified Insight phải có Evidence Trail, failed actions và repair budget, clarification recall/precision, latency và API cost report. Mỗi check trả pass/fail/not-applicable cùng denominator; not-applicable không được âm thầm biến thành pass. Acceptance: các repro false-positive ở §4 đều fail; có test negative cho wrong metric, wrong filter, unsupported claim, orphan insight và exhausted repair budget.

### Bước 4 — Sửa contract chart và phân loại failure

Quyết định rõ KPI có phải valid chart type không. Giữ nguyên v9 cases và kết quả lịch sử sau run; không sửa case v9 retroactively. Tạo corrected case version hoặc clean cases mới, và nếu cần thì chạy một regrade riêng có nhãn, liên kết tới case/grader version mới. Báo cáo riêng case-spec/grader defect, agent defect, provider error và infra error. Acceptance: chart gate chỉ dùng cases có expectation hợp lệ; bốn chart-only false negative không còn làm sai kết luận, hai invalid/garbled output vẫn được chấm như agent behavior nếu contract xác nhận đó là lỗi.

### Bước 5 — Bảo vệ holdout và đo generalization

Tách quyền ghi bộ clean holdout; hash cả JSON case và fixture; kiểm tra không có case ID/dataset overlap; cấm evaluator tự chọn development khi chạy release. Chạy hidden holdout với provider/model version đã ghi, và thêm mutation cases đổi metric, filter, grouping, date key, evidence và clarification. Acceptance: có audit log chứng minh case chưa dùng để sửa prompt/grader; score được báo cáo theo tier và theo case, kèm confidence interval hoặc ít nhất run-level variance.

### Bước 6 — Đồng nhất CI/Docker và release check

Cho workflow cài bằng constraints giống Docker, thêm Docker build target 'check', chạy evaluator frozen suite trong job riêng và lưu artifact summary/raw trace. Acceptance: clean CI build, Docker target check, Ruff, format, mypy, pytest và release evaluator cùng pass trong một commit; README/mvp-acceptance chỉ nâng claim sau khi links tới artifact tồn tại.

### Bước 7 — Reassess overfit và quyết định release

Rerun sau các bước trên trên suite frozen và holdout chưa thấy. Release chỉ khi mọi hard gate §17.4 đạt, E2E ≥85%, expected/schema/chart/evidence/tool/clarification có evidence đầy đủ, không có unresolved contract false negative, và limitations ghi đúng những gì chưa kiểm tra live. Nếu chỉ đạt test/lint/type offline thì giữ trạng thái pre-release.

## 8. Acceptance checklist ngắn

- [ ] Một manifest duy nhất có ≥40 case unique theo §17.3, phủ bốn dataset tiers, cùng commit/runner/model/config; clean denominator được tách khỏi development và contaminated regression reports.
- [ ] Mọi metric có tử số/mẫu số; expected-value accuracy được tách khỏi calculation case-run check; phân biệt case, run, answered, chart-expected, clarification-required và provider error.
- [ ] Expected values ≥95%, schema 100%, unsupported claim ≤2%, tool execution ≥95%, chart ≥95%, evidence 100%, clarification recall ≥90%, E2E ≥85%.
- [ ] Negative tests bắt được wrong filter, wrong metric, unsupported conclusion, orphan evidence và repair budget exhaustion.
- [ ] Chart KPI contract được quyết định và các failure được phân loại đúng.
- [ ] Clean holdout không overlap và không bị dùng để tuning; raw trace/case-dataset hash được lưu.
- [ ] CI dùng constraints giống Docker; Docker build/target 'check' đã chạy; evaluator artifact được lưu.
- [ ] Live Gemini, latency/cost và deployment verification được chạy trong môi trường có quyền, hoặc được ghi rõ là chưa verified.
- [ ] README và 'docs/mvp-acceptance.md' phản ánh v9/frozen release evidence; portfolio artifacts vẫn ở roadmap theo quyết định user.

## 9. Các điểm chưa thể xác minh trong audit này

Không thể kiểm tra live Gemini, provider drift, latency/cost thực tế hoặc Docker build vì task cấm live/secrets và máy audit không có Docker. Không có release manifest/raw eval artifact đã commit để xác minh source commit, model/config và freeze status của từng run. Vì vậy các mục này được đánh dấu UNVERIFIED, không được suy diễn thành đạt hoặc không đạt. Kết luận HOLD dựa trên các failure và lỗ hổng grader đã tái hiện offline.
