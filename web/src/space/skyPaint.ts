const TAU = Math.PI * 2

function seedRng(seed: number): () => number {
  let s = seed % 2147483647
  if (s <= 0) s += 2147483646
  return () => {
    s = (s * 16807) % 2147483647
    return (s - 1) / 2147483646
  }
}

function glow(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  radius: number,
  rgb: [number, number, number],
  alpha: number,
) {
  const g = ctx.createRadialGradient(x, y, 0, x, y, radius)
  g.addColorStop(0, `rgba(${rgb[0]},${rgb[1]},${rgb[2]},${alpha})`)
  g.addColorStop(0.4, `rgba(${rgb[0]},${rgb[1]},${rgb[2]},${alpha * 0.35})`)
  g.addColorStop(1, 'rgba(0,0,0,0)')
  ctx.fillStyle = g
  ctx.beginPath()
  ctx.arc(x, y, radius, 0, TAU)
  ctx.fill()
}

export function paintBackdrop(canvas: HTMLCanvasElement) {
  const ctx = canvas.getContext('2d')
  if (!ctx) return
  const w = canvas.width
  const h = canvas.height
  const rng = seedRng(8821)
  const m = Math.max(w, h)

  ctx.fillStyle = '#070b14'
  ctx.fillRect(0, 0, w, h)

  const wash = ctx.createRadialGradient(w * 0.5, h * 0.48, m * 0.08, w * 0.5, h * 0.5, m * 0.72)
  wash.addColorStop(0, '#101828')
  wash.addColorStop(1, '#060910')
  ctx.fillStyle = wash
  ctx.fillRect(0, 0, w, h)

  ctx.globalCompositeOperation = 'lighter'
  glow(ctx, w * 0.18, h * 0.2, m * 0.28, [70, 110, 190], 0.38)
  glow(ctx, w * 0.82, h * 0.22, m * 0.24, [48, 86, 150], 0.3)
  glow(ctx, w * 0.78, h * 0.78, m * 0.26, [86, 120, 188], 0.28)
  glow(ctx, w * 0.2, h * 0.8, m * 0.25, [40, 78, 130], 0.3)
  glow(ctx, w * 0.58, h * 0.36, m * 0.16, [110, 150, 210], 0.16)
  glow(ctx, w * 0.4, h * 0.66, m * 0.14, [64, 120, 168], 0.12)

  for (let i = 0; i < 10; i += 1) {
    glow(
      ctx,
      rng() * w,
      rng() * h,
      m * (0.06 + rng() * 0.1),
      [50 + rng() * 50, 80 + rng() * 50, 130 + rng() * 50],
      0.05 + rng() * 0.08,
    )
  }

  ctx.globalCompositeOperation = 'source-over'
  const stars = Math.floor((w * h) / 1400)
  for (let i = 0; i < stars; i += 1) {
    const x = rng() * w
    const y = rng() * h
    const roll = rng()
    const a = 0.18 + rng() * 0.72
    ctx.fillStyle = roll > 0.86 ? `rgba(255,236,210,${a})` : `rgba(226,234,255,${a})`
    const s = roll > 0.93 ? 2 : 1
    ctx.fillRect(x, y, s, s)
    if (roll > 0.97) {
      ctx.fillStyle = `rgba(220,230,255,${a * 0.35})`
      ctx.fillRect(x - 2, y, 5, 1)
      ctx.fillRect(x, y - 2, 1, 5)
    }
  }

  const edge = ctx.createRadialGradient(w * 0.5, h * 0.46, m * 0.22, w * 0.5, h * 0.5, m * 0.7)
  edge.addColorStop(0, 'rgba(0,0,0,0)')
  edge.addColorStop(1, 'rgba(3,5,10,0.5)')
  ctx.fillStyle = edge
  ctx.fillRect(0, 0, w, h)
}
