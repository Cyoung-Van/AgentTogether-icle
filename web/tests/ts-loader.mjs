import ts from 'typescript'
import { readFile } from 'node:fs/promises'
export async function resolve(specifier, context, next) {
  if (specifier.startsWith('.') && !/\.[a-z]+$/.test(specifier)) {
    for (const ext of ['.ts', '.tsx']) {
      try { return await next(specifier + ext, context) } catch { /* try next suffix */ }
    }
  }
  return next(specifier, context)
}
export async function load(url, context, next) {
  if (/\.tsx?$/.test(url)) {
    const source = await readFile(new URL(url), 'utf8')
    return { format: 'module', shortCircuit: true, source: ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2023, module: ts.ModuleKind.ESNext, jsx: ts.JsxEmit.ReactJSX } }).outputText }
  }
  return next(url, context)
}
