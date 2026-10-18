// Labels are Vietnamese for the user; the text sent to the models stays English, which they follow far better.
export type QuickPrompt = readonly [label: string, text: string]

export const QUICK_SUBJECTS: ReadonlyArray<QuickPrompt> = [
  ['Cây cối', 'a stylized tree with lush foliage, whole tree from roots to crown'],
  ['Bụi cỏ & hoa', 'a small bush with flowers and grass tufts'],
  ['Đá', 'a cluster of mossy rocks and boulders'],
  ['Pha lê', 'a glowing crystal cluster ore deposit'],
  ['Nhà cửa', 'a small house with roof, door and windows'],
  ['Vũ khí', 'a single ornate sword'],
  ['Rương báu', 'a treasure chest'],
  ['Bình thuốc', 'a potion bottle with glowing liquid'],
  ['Thùng & hộp', 'a wooden barrel and crate prop'],
  ['Nhân vật', 'a full-body hero character standing'],
  ['Quái vật', 'a monster creature, full body'],
  ['Linh thú', 'a mythical beast companion, full body']
]

export const QUICK_STYLES: ReadonlyArray<QuickPrompt> = [
  ['Trung cổ', 'medieval fantasy style, weathered wood, iron and stone, earthy colors'],
  ['Anime', 'anime style, cel shading, clean lineart, vibrant colors'],
  ['Tu tiên', 'xianxia cultivation fantasy style, ancient Chinese aesthetic, jade and gold, flowing silk, ethereal spiritual glow'],
  ['Nhật cổ', 'feudal Japan style, lacquered wood, paper and bronze details'],
  ['Chibi', 'chibi style, cute big-head proportions'],
  ['Hoạt hình', 'stylized hand-painted cartoon style of a mobile game'],
  ['Pixel art', 'pixel art style, crisp pixels, limited palette'],
  ['Low-poly', 'low-poly 3D style, flat shaded facets'],
  ['Dark fantasy', 'dark fantasy style, gothic, desaturated, ominous'],
  ['Sci-fi', 'sci-fi style, sleek metal with neon accents']
]

export const quickStyleLabel = (text: string | null): string | null => QUICK_STYLES.find(([, t]) => t === text)?.[0] ?? null
