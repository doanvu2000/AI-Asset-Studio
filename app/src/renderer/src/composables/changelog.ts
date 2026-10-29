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
