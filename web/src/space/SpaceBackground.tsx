import { useEffect, useRef } from 'react'
import { paintBackdrop } from './skyPaint'

export default function SpaceBackground() {
  const ref = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    const canvas = ref.current
    if (!canvas) return
    const paint = () => {
      const dpr = Math.min(window.devicePixelRatio || 1, 1.75)
      canvas.width = Math.max(1, Math.floor(window.innerWidth * dpr))
      canvas.height = Math.max(1, Math.floor(window.innerHeight * dpr))
      paintBackdrop(canvas)
    }
    paint()
    window.addEventListener('resize', paint)
    return () => window.removeEventListener('resize', paint)
  }, [])

  return (
    <div className="space-sky" aria-hidden="true">
      <div className="space-sky-far" />
      <canvas ref={ref} className="space-sky-canvas" />
      <div className="space-sky-near" />
    </div>
  )
}
