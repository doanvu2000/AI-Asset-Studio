import { ref } from 'vue'

export interface Release {
  version: string
  /** Tag date, YYYY-MM-DD. */
  date: string
  items: string[]
}

export const APP_VERSION: string = __APP_VERSION__

const SEEN_KEY = 'studio.whatsNew.seen'

export const whatsNewOpen = ref(false)

// Summaries of `git log <prev tag>..<tag>` and the matching .planning plans; newest first.
export const CHANGELOG: Release[] = [
  {
    version: '1.2.0',
    date: '2026-10-30',
    items: [
      'Claude Code và Codex CLI điều khiển được AI Asset Studio qua MCP, skill và plugin cài 1 chạm; mọi thao tác của agent hiện trực tiếp trên app',
      'Plugin Unity và Godot: chạy quy trình, duyệt bước chờ và nhận asset thẳng vào project ngay trong editor; cài từ Cài đặt → Công cụ',
      'Màn Kiểm tra project: soát texture, ảnh trùng, asset không dùng, mesh, âm thanh; sửa .meta / .import 1 chạm có backup và hoàn tác',
      'Địa hình 3D: mô tả → Claude đặt núi, hồ, biển, sông → heightmap 16-bit có xói mòn, 4 lớp texture PBR, xuất Terrain Unity / mesh LOD Godot',
      'Trim sheet lặp ngang liền mạch + PBR, bộ decal nền trong và bộ mảnh modular theo lưới; prefab Unity, MeshLibrary GridMap Godot',
      'Bộ VFX hạt 7 preset (nổ, hồi máu, nhặt đồ, vệt đạn, bụi, tia va chạm, hào quang) → ParticleSystem Unity, GPUParticles Godot',
      'Màn UI game: Claude bố cục từ ui-kit, mockup 16:9 / 20:9 / 4:3 trong vùng an toàn, xuất prefab UGUI + UXML và scene Godot + Theme',
      'Vật phẩm xoay vòng, UI động shine / glow / nhún / lấp lánh, vật lý Spine 4.2 cho tóc, áo choàng, đuôi',
      'Minimap từ ảnh màn, Tiled hoặc LDtk, có icon điểm đánh dấu và mặt nạ tròn',
      'Màn chơi thử: gom nhân vật, ô nền, prop của dự án → Claude xếp màn → chạy thử trong Godot / Unity, xuất thêm Tiled .tmx và LDtk, nút “Mở trong Godot / Unity”',
      'Tile động lặp liền mép: nước, dung nham, cỏ gió (CPU) hoặc đuốc, thác (Wan), nhận cả autotile 47 ô; TileSet Godot, AnimatedTile / RuleTile Unity',
      'Icon kho đồ từ mesh 3D: cả bộ cùng góc, đèn, độ lấp đầy, có viền / nền độ hiếm + atlas',
      'Isometric 2:1: tile mặt đất liền mép có biến thể, prop 4 hướng đứng đúng ô; Tilemap Isometric Unity, TileSet y-sort Godot',
      'Bộ thẻ bài từ CSV hoặc chủ đề: khung theo độ hiếm và loại, chữ tiếng Việt tự co vừa ô, mặt sau, prefab UGUI và scene Godot',
      'Biểu cảm mặt Spine: mắt, miệng, mày thành slot riêng, anim chớp mắt, nói, vui, giận, buồn; áo choàng lắc tự nhiên hơn',
      'Phóng to HD thêm engine SeedVR2 (tuỳ chọn, cài 1 chạm): nét và giàu chi tiết hơn Real-ESRGAN, giữ nền trong',
      'Màn UI tự dùng TextMeshPro và vẽ icon theo chủ đề; Kiểm tra project bắt ảnh gần giống bằng CLIP',
      'Sửa: preview Unity không còn hồng, terrain có mặt nước hồ / sông, decal hết ra vòng tròn, trim đúng màu gỗ, VFX trễ / kéo dài theo vận tốc, nhiều bản xuất chung một project Unity không trùng script',
      'Chạy nhanh hơn: bước Claude và bước tất định dùng lại kết quả khi đầu vào y hệt (tuỳ chọn “Bỏ qua kết quả cũ”)',
      'Trang Quy trình dùng hết bề ngang khi màn hình rộng',
      'Nhập nhiều bản xuất vào cùng project Unity / Godot: script dùng chung (NpcData, Parallax, class_name…) được dùng lại, không còn trùng class',
      'Màn chơi thử: nhân vật không có anim walk nhún theo bước thay vì trượt, tường top-down có mặt trước + bóng đổ; .tmx / .ldtk đúng chuẩn',
      'Tile động đuốc / thác khớp tông mép giữa các frame, lát liền hơn; Godot có scene demo mở là chạy',
      'Biểu cảm mặt Spine: mặt bị tóc hoặc mũ che một phần vẫn làm phần còn thấy; mày dưới tóc giữ nguyên tóc, hết viền mờ',
      'Icon 3D: chỉ chọn thư mục cũng chạy, mesh hỏng bỏ qua từng file; kiểu toon có khối, AO và viền sáng',
      'Bộ thẻ bài: chữ tự đổi sang TextMeshPro khi project có TMP; thẻ thiếu mô tả art thì Claude viết, vẽ theo ảnh style không còn chép ảnh mẫu',
      'Decal vết nứt nét đậm, đọc rõ; preview Godot của VFX hạt 3D không còn trống',
      'Phóng to SeedVR2 nhận ảnh lớn không tràn VRAM; màn UI vẽ icon nhanh hơn và không để ô icon trống',
    ]
  },
  {
    version: '1.1.0',
    date: '2026-10-29',
    items: [
      'Màn khởi động có logo động, hiện trạng thái core rồi mờ dần vào app',
      'Hộp “Có gì mới”, mở lại được trong Cài đặt và Ctrl K',
      'Hoạt ảnh sprite từ 1 ảnh: Wan2.2 tạo chuyển động, tự tìm đoạn lặp, khoá pivot, ra sprite sheet',
      'Skybox 360: panorama liền mép (Qwen-Image + LoRA 360), xem xoay 360 trong app, xuất skybox Unity / Godot',
      'Đổi style hàng loạt ngay trong project Unity / Godot, giữ GUID và .meta; có chạy thử, backup và hoàn tác',
      'Prop 3D phá huỷ được: chia mảnh Voronoi, texture mặt cắt, prefab nguyên / vỡ có vật lý',
      'Tô lại texture cho mesh có sẵn (.glb / .fbx / .obj) từ nhiều góc nhìn, giữ hình và UV',
      'Điểm trúng đòn tự dò không còn rơi vào đầu nhân vật chibi',
      'Gộp atlas cho mobile từ nhiều asset (Unity, Godot) và ngân sách mobile: nén ASTC / ETC2, bảng RAM GPU trước / sau',
      'Xuất và nhập Aseprite (.aseprite + JSON sheet, tag theo clip và hướng)',
      'Biến thể kẻ địch: đổi palette cả bộ anim 8 hướng, thêm phụ kiện bằng Klein',
      'Hiệu ứng trúng đòn: gắn VFX vào khung đánh, xuất AnimationEvent cho Unity và signal cho Godot',
      'Tách ảnh thành các lớp RGBA (Qwen-Image-Layered), xuất PNG, PSD và stack lớp cho Unity / Godot',
      '“Dùng tiếp cho” nối các quy trình, Bộ nhân vật, phóng to ảnh ESRGAN, danh sách asset từ GDD',
      'UI kit, icon và 9-slice; vật liệu PBR; bộ icon cho store (iOS, Android adaptive, Steam, web)',
      'Nhân vật 3D có rig (GLB / FBX, xương Humanoid, clip bake sẵn) và xem trước trong engine',
      'Xuất engine tự thêm collider và pivot ở chân; quy trình độ hiếm vật phẩm',
      'Biến thể ngày / đêm / mùa bằng LUT; âm thanh nền theo cảnh (nhạc + môi trường)',
      'Trạng thái hư hại kèm mảnh văng; preset shader sprite cho Godot và Unity',
      'Nền parallax nhiều lớp; dịch chữ nằm trong ảnh',
      'Key art động lặp vòng (Wan2.2 TI2V-5B); autotile blob-47; normal map 2D cho sprite sheet',
      'Bitmap font (có dấu tiếng Việt) và paper-doll gắn đồ theo xương',
      'Style bible của dự án áp vào mọi lượt chạy mới',
      'Gói NPC: chân dung, thoại, giọng đọc TTS chạy trên CPU',
      'Tìm asset giống và phát hiện trùng (CLIP + hash ảnh)',
      'Storyboard thành animatic: kế hoạch shot, khung hình nhất quán, lồng tiếng, ghép video',
      'Đóng gói asset pack local: Unity UPM, addon Godot, nguồn gốc, CHANGELOG, license',
      'Âm thanh SFX và nhạc lặp (Stable Audio, ACE-Step); VFX flipbook; xuất cả hai sang Unity / Godot',
      '3D game-ready: remesh hoặc decimate, bake normal / AO, LOD',
      'Spine: anim từ video tham chiếu, preset đi / chạy / nhảy / đánh / trúng đòn / chết, biến thể skin',
      'Sửa lỗi sau khi kiểm bằng Unity thật: material sprite có normal map, FBX Humanoid, GUID không trùng'
    ]
  },
  {
    version: '1.0.0',
    date: '2026-10-28',
    items: [
      'Cài một chạm: ComfyUI, gói model, Blender và Claude',
      'Sprite 8 hướng, chu kỳ đi bộ qua auto-rig Blender, bộ anim và normal map',
      'Rig Spine 6 clip kèm góc nhìn sau: tóc, áo choàng, tay áo rộng, vũ khí cầm tay',
      'Texture seamless, tileset Wang theo góc, viền / bóng sprite, chế độ pixel-art',
      'Chấm điểm chất lượng (QA) trong Thư viện: lọc, sắp xếp, xem tile và vật liệu',
      'Dự án: danh sách, chi tiết, chỉnh style; tạo mới và chạy hàng loạt theo dự án',
      'Thư viện chọn nhiều và so sánh; xuất asset theo preset Unity / Godot, kéo thả ra ngoài',
      'Duyệt ngay trong lượt chạy, tạo lại ảnh, lời khuyên khi lỗi; dịch prompt Việt → Anh',
      'Trình sửa quy trình giữ chú thích YAML; prompt nhanh và ước lượng thời gian',
      'Giao diện kiểu macOS, sáng / tối, thanh bên có badge, trang Tổng quan và Bắt đầu'
    ]
  }
]

/** Shown once per app version, including a fresh install (nothing seen yet). */
export function shouldShowWhatsNew(seen: string | null, current: string): boolean {
  return !!current && seen !== current
}

export function seenVersion(): string | null {
  try {
    return localStorage.getItem(SEEN_KEY)
  } catch {
    // Storage blocked: treat this version as seen rather than open the dialog on every launch.
    return APP_VERSION
  }
}

export function markSeen(version: string = APP_VERSION): void {
  try {
    localStorage.setItem(SEEN_KEY, version)
  } catch {
    return
  }
}
