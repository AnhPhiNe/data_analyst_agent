# Kế hoạch rà soát toàn dự án — 2026-09-15

## Phạm vi và mốc kiểm tra

- Đối chiếu toàn bộ workspace với `Spec.md` v1.4 và các sửa đổi đã được chấp thuận; không chỉ xét git diff.
- HEAD bắt đầu: `c6f703f2c7a06d1430aa6b3258c7fbeeef53e8d9`.
- Giữ nguyên tệp có sẵn chưa được theo dõi `docs/RESUME-HANDOFF.md`.
- Chỉ rà soát, kiểm chứng và lập kế hoạch sửa; không thay đổi mã triển khai, fixture hoặc kỳ vọng holdout.
- Không gọi API model trực tiếp, đọc bí mật hoặc cài thêm dependency trong đợt này.

## Phân công Luna Max

| Nhánh | Phạm vi | Sản phẩm |
|---|---|---|
| spec_compliance | FR-01–13, ranh giới kiến trúc, ingestion, phiên, bảo mật, thống kê, verification, UI và export | `review-spec-compliance.md`: ma trận yêu cầu và bằng chứng triển khai |
| overfit_architecture | Prompt, orchestration, gateway, coupling, mã trùng, logic đặc thù, abstraction dư | `review-overfit-architecture.md`: lỗi có bằng chứng và cơ hội đơn giản hóa |
| evaluation_release | Grader, holdout, quality gates, test, cấu hình CI/Docker, tài liệu nghiệm thu | `review-evaluation-release.md`: tính hợp lệ của bằng chứng và khả năng phát hành |
| Điều phối chính | Giám sát, kiểm tra lại bằng chứng, loại phát hiện sai/trùng, tổng hợp thứ tự sửa | `review-summary.md`: kết luận và backlog có điều kiện nghiệm thu |

## Trình tự

1. Đọc Spec và các điều chỉnh; phân biệt Must, Should, Could và quyết định hoãn đã ghi nhận.
2. Khám phá mã bằng knowledge graph trước; xác nhận nguồn hiện tại vì index có dấu hiệu cũ. Dùng tìm kiếm file khi graph thiếu dữ liệu.
3. Mỗi nhánh lập ánh xạ yêu cầu → điểm triển khai → kiểm thử/bằng chứng → khoảng trống.
4. Kiểm tra lỗi bằng tình huống cụ thể hoặc kiểm thử offline. Một điểm phức tạp chỉ là ứng viên refactor nếu chưa chứng minh ảnh hưởng.
5. Nhánh đánh giá chạy suite/test/lint/typecheck khả dụng; ghi rõ chưa chạy UI, Docker hoặc live evaluation nếu thiếu điều kiện.
6. Điều phối kiểm tra lại các phát hiện nghiêm trọng, yêu cầu tái hiện hoặc sửa kết luận khi bằng chứng không đủ.
7. Tổng hợp backlog theo dependency: lỗi tính đúng đắn/an toàn → độ tin cậy đánh giá → refactor cần thiết → nghiệm thu phát hành; ghi riêng hạng mục có thể hoãn.

## Tiêu chuẩn kết luận

- Mỗi lỗi cần đường dẫn và dòng mã, điều khoản Spec, tác động, cách tái hiện và cách sửa tối thiểu.
- Phân biệt: lỗi xác nhận; khoảng trống bằng chứng; heuristic thiết kế; hạn chế được Spec chấp thuận.
- Không coi typed contracts, verification, quota hoặc checkpoint là overengineering chỉ vì có nhiều mã: đó là các yêu cầu rõ trong Spec.
- Không kết luận overfitting chỉ từ điểm holdout giảm: kiểm tra contamination, grader, loại câu hỏi, dữ liệu và phiên bản prompt trước.
- Không dùng các báo cáo trên nhiều phiên bản như một release suite đồng nhất.
- Mỗi việc sửa cần tiêu chí hoàn thành và kiểm thử hồi quy theo lớp hành vi, tránh vá riêng câu hỏi đã thấy.

## Điều kiện hoàn thành đợt rà soát

Ba báo cáo nhánh, kết quả kiểm tra khả dụng và báo cáo tổng hợp có thứ tự ưu tiên được lưu trong `docs/`. Những phần chưa kiểm chứng được ghi rõ, không suy diễn rằng test pass đồng nghĩa sản phẩm đã đạt nghiệm thu.
