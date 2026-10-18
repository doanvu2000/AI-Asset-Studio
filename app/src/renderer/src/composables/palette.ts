import { ref } from 'vue'

export const paletteOpen = ref(false)
export const shortcutsOpen = ref(false)

export function openPalette(): void {
  shortcutsOpen.value = false
  paletteOpen.value = true
}

export function openShortcuts(): void {
  paletteOpen.value = false
  shortcutsOpen.value = true
}

/** Accent- and case-insensitive match so "tao moi" finds "Tạo mới". */
export function fold(s: string): string {
  return s
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .replace(/đ/g, 'd')
    .replace(/Đ/g, 'D')
    .toLowerCase()
}
