# Ý tưởng nâng cấp RAG và LLM

## 1. Nâng cấp RAG/intent routing

- Dùng top intent ứng viên thay vì chỉ lấy một kết quả gần nhất.
- Dùng cả **ngưỡng khớp tối thiểu** và **khoảng cách giữa intent hạng nhất với hạng nhì**; dưới ngưỡng hoặc quá sát nhau thì hỏi lại.
- Nhận diện câu phủ định và yêu cầu nhiều ý. Nếu tách chắc được, xử lý từng ý riêng; nếu không, hỏi rõ.
- Mở rộng Intents DB bằng mô tả intent, tham số cần có, hành động/tool được phép và ví dụ đối lập như subscribe vs unsubscribe.
- Đồng bộ cache embedding khi prompt hoặc model embedding đổi.
- Chỉ gọi endpoint sau khi Python xác thực intent, action và target thuộc allowlist.

## 2. Nâng cấp LLM

- Giữ GPT ở các việc cần hiểu ngôn ngữ: diễn giải yêu cầu, xử lý câu nhiều ý, hỏi lại khi thiếu thông tin và phân tích kết quả analytics.
- Chuẩn hóa đầu ra của LLM thành cấu trúc dễ kiểm tra, ví dụ danh sách tác vụ gồm intent, metric/action và target.
- Với analytics, cung cấp dữ liệu backend cùng metadata như nguồn và thời điểm; yêu cầu LLM chỉ kết luận từ dữ liệu đó.
- Khi dữ liệu trống, backend lỗi hoặc bằng chứng không đủ, trả lời rõ tình trạng thay vì đoán.
- Không để LLM tự thực thi command tùy ý; lớp Python vẫn kiểm tra và gọi tool cụ thể.

## 3. Nâng cấp Visualize

- Nối dữ liệu truy vấn từ Prometheus/NWDAF trực tiếp vào pipeline hiển thị, thay vì chỉ đọc CSV tĩnh.
- Để Python dựng biểu đồ từ dữ liệu backend đã xác thực; LLM có thể đề xuất loại biểu đồ và phân tích kết quả, nhưng không tự tạo dữ liệu hay mã vẽ tùy ý.
- Chọn biểu đồ theo metric: active UE theo thời gian (đường), trạng thái đăng ký (timeline), handover giữa các cell (luồng/animation), lượt ghé địa điểm (cột hoặc timeline).
- Hiển thị biểu đồ cùng nhận xét của LLM và thông tin nguồn/thời gian dữ liệu để người dùng đối chiếu.
- Ghi rõ tọa độ và chuyển động hiện tại là mô phỏng; không trình bày như dữ liệu địa lý thực.

## 4. Phân biệt và khoanh vùng lỗi

- Thu thập log và metric liên quan từ UE/UERANSIM, Free5GC, NWDAF và Prometheus trong cùng khoảng thời gian.
- Theo code hiện có, AMF có log, endpoint metric riêng ở cổng `8100` và metric `number_of_gNbs`; AMF cũng gửi sự kiện đăng ký/vị trí sang NWDAF để xuất các metric như `active_UEs` và `UE_location_report`.
- SMF có log; sự kiện PDU session được NWDAF chuyển thành `active_pdu_sessions` và `total_pdu_session_events`, xuất qua endpoint `/metrics` của NWDAF thay vì exporter riêng trong SMF.
- NWDAF có log và tổng hợp/xuất metric AMF/SMF qua `/metrics`. UERANSIM không phải NF nhưng có log và metric mô phỏng `ue_destination_visits_total`.
- Các NF như UPF, AUSF, UDM, PCF và NRF có log trong code; chưa thấy metric Prometheus riêng được cấu hình trong project.
- Cấu hình scrape trong `prometheus/prometheus.yml` chưa chứng minh endpoint đang chạy hoặc được scrape thành công; cần kiểm tra trạng thái scrape trước khi dùng metric làm bằng chứng.
- Chuẩn hóa timestamp và liên kết sự kiện theo UE, request hoặc subscription để lần theo luồng lỗi giữa các thành phần.
- Dùng rule xác định cho dấu hiệu rõ ràng như timeout, HTTP error hoặc thiếu sự kiện; dùng RAG để tìm tài liệu/runbook liên quan.
- Để LLM tóm tắt lỗi nghi vấn, thành phần liên quan, bằng chứng, mức độ chắc chắn và bước kiểm tra tiếp theo; không để LLM tự khẳng định nguyên nhân khi bằng chứng chưa đủ.
- Nếu log/metric thiếu hoặc mâu thuẫn, báo chưa đủ dữ liệu thay vì đoán root cause.
- Giới hạn quyền đọc log và che thông tin định danh UE như SUPI khi không cần thiết.
- Bắt đầu với một loại sự cố cụ thể, chẳng hạn UE đăng ký thất bại, rồi mở rộng sau khi kiểm chứng độ chính xác.
