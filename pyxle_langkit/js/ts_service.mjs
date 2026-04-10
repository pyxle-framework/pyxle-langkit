/**
 * TypeScript Language Service worker for Pyxle .pyx files.
 *
 * Communicates with the Python LSP server via NDJSON over stdin/stdout.
 * Provides completions, hover (quickInfo), and go-to-definition for
 * JSX sections by maintaining in-memory virtual .tsx documents.
 *
 * Protocol (one JSON object per line):
 *
 *   Request:  {"id": 1, "method": "update", "params": {"file": "/abs/path.pyx", "content": "...", "projectRoot": "/abs/root"}}
 *   Request:  {"id": 2, "method": "completions", "params": {"file": "/abs/path.pyx", "line": 10, "character": 5}}
 *   Request:  {"id": 3, "method": "quickInfo", "params": {"file": "/abs/path.pyx", "line": 10, "character": 5}}
 *   Request:  {"id": 4, "method": "definition", "params": {"file": "/abs/path.pyx", "line": 10, "character": 5}}
 *   Request:  {"id": 5, "method": "remove", "params": {"file": "/abs/path.pyx"}}
 *   Response: {"id": 1, "result": ...}
 *   Response: {"id": 2, "error": "message"}
 */

import { createRequire } from 'node:module';
import { readFileSync } from 'node:fs';
import { dirname } from 'node:path';
import { createInterface } from 'node:readline';

// Use createRequire to find typescript from the project's node_modules,
// respecting NODE_PATH and the working directory.
const require = createRequire(process.cwd() + '/index.js');
const ts = require('typescript');

// ---------------------------------------------------------------------------
// Virtual file store
// ---------------------------------------------------------------------------

/** Map from real .pyx path → virtual .tsx content */
const virtualFiles = new Map();
/** Map from real .pyx path → version counter */
const fileVersions = new Map();
/** Project root (set on first `update` call) */
let projectRoot = process.cwd();
/** Cached compiler options */
let compilerOptions = null;

function toVirtualPath(pyxPath) {
    return pyxPath + '.tsx';
}

function fromVirtualPath(tsxPath) {
    if (tsxPath.endsWith('.pyx.tsx')) {
        return tsxPath.slice(0, -4); // Remove .tsx → .pyx
    }
    return tsxPath;
}

// ---------------------------------------------------------------------------
// LanguageServiceHost
// ---------------------------------------------------------------------------

function getCompilerOptions() {
    if (compilerOptions) return compilerOptions;

    // Try to read the project's tsconfig.json or jsconfig.json
    const tsconfigPath = ts.findConfigFile(projectRoot, ts.sys.fileExists, 'tsconfig.json')
        || ts.findConfigFile(projectRoot, ts.sys.fileExists, 'jsconfig.json');

    if (tsconfigPath) {
        const configFile = ts.readConfigFile(tsconfigPath, ts.sys.readFile);
        if (configFile.config) {
            const parsed = ts.parseJsonConfigFileContent(configFile.config, ts.sys, dirname(tsconfigPath));
            compilerOptions = {
                ...parsed.options,
                jsx: ts.JsxEmit.ReactJSX,
                allowJs: true,
                checkJs: false,
                noEmit: true,
                strict: false,
                skipLibCheck: true,
            };
            return compilerOptions;
        }
    }

    // Fallback defaults
    compilerOptions = {
        target: ts.ScriptTarget.ES2022,
        module: ts.ModuleKind.ESNext,
        moduleResolution: ts.ModuleResolutionKind.Bundler,
        jsx: ts.JsxEmit.ReactJSX,
        allowJs: true,
        checkJs: false,
        noEmit: true,
        strict: false,
        skipLibCheck: true,
        esModuleInterop: true,
        baseUrl: projectRoot,
    };
    return compilerOptions;
}

/** @type {ts.LanguageServiceHost} */
const serviceHost = {
    getCompilationSettings: () => getCompilerOptions(),
    getScriptFileNames: () => {
        const names = [];
        for (const pyxPath of virtualFiles.keys()) {
            names.push(toVirtualPath(pyxPath));
        }
        return names;
    },
    getScriptVersion: (fileName) => {
        const pyxPath = fromVirtualPath(fileName);
        return String(fileVersions.get(pyxPath) ?? 0);
    },
    getScriptSnapshot: (fileName) => {
        // Check virtual files first
        const pyxPath = fromVirtualPath(fileName);
        if (virtualFiles.has(pyxPath)) {
            return ts.ScriptSnapshot.fromString(virtualFiles.get(pyxPath));
        }
        // Fall through to real filesystem for .js/.jsx/.ts/.tsx files
        try {
            const content = readFileSync(fileName, 'utf-8');
            return ts.ScriptSnapshot.fromString(content);
        } catch {
            return undefined;
        }
    },
    getCurrentDirectory: () => projectRoot,
    getDefaultLibFileName: (options) => ts.getDefaultLibFilePath(options),
    fileExists: (fileName) => {
        const pyxPath = fromVirtualPath(fileName);
        if (virtualFiles.has(pyxPath)) return true;
        return ts.sys.fileExists(fileName);
    },
    readFile: (fileName) => {
        const pyxPath = fromVirtualPath(fileName);
        if (virtualFiles.has(pyxPath)) return virtualFiles.get(pyxPath);
        return ts.sys.readFile(fileName);
    },
    readDirectory: ts.sys.readDirectory,
    directoryExists: ts.sys.directoryExists,
    getDirectories: ts.sys.getDirectories,
    resolveModuleNames: (moduleNames, containingFile) => {
        const options = getCompilerOptions();
        return moduleNames.map(name => {
            // For relative imports from a .pyx.tsx file, resolve relative
            // to the original .pyx file's directory (not the virtual path).
            const resolveDir = dirname(fromVirtualPath(containingFile));

            const resolved = ts.resolveModuleName(name, containingFile, options, {
                ...ts.sys,
                fileExists: (f) => {
                    const pyx = fromVirtualPath(f);
                    if (virtualFiles.has(pyx)) return true;
                    return ts.sys.fileExists(f);
                },
                readFile: (f) => {
                    const pyx = fromVirtualPath(f);
                    if (virtualFiles.has(pyx)) return virtualFiles.get(pyx);
                    return ts.sys.readFile(f);
                },
            });
            return resolved.resolvedModule;
        });
    },
};

const documentRegistry = ts.createDocumentRegistry();
const service = ts.createLanguageService(serviceHost, documentRegistry);

// ---------------------------------------------------------------------------
// Position utilities
// ---------------------------------------------------------------------------

function lineCharToOffset(content, line, character) {
    let offset = 0;
    let currentLine = 0;
    for (let i = 0; i < content.length; i++) {
        if (currentLine === line) {
            return offset + character;
        }
        if (content[i] === '\n') {
            currentLine++;
            offset = i + 1;
        }
    }
    return offset + character;
}

function offsetToLineChar(content, offset) {
    let line = 0;
    let lineStart = 0;
    for (let i = 0; i < offset && i < content.length; i++) {
        if (content[i] === '\n') {
            line++;
            lineStart = i + 1;
        }
    }
    return { line, character: offset - lineStart };
}

// ---------------------------------------------------------------------------
// Request handlers
// ---------------------------------------------------------------------------

function handleUpdate(params) {
    const { file, content, projectRoot: root } = params;
    if (root && root !== projectRoot) {
        projectRoot = root;
        compilerOptions = null; // Reset so we re-read tsconfig
    }
    virtualFiles.set(file, content);
    fileVersions.set(file, (fileVersions.get(file) ?? 0) + 1);
    return { ok: true };
}

function handleRemove(params) {
    virtualFiles.delete(params.file);
    fileVersions.delete(params.file);
    return { ok: true };
}

function handleCompletions(params) {
    const { file, line, character } = params;
    const virtualPath = toVirtualPath(file);
    const content = virtualFiles.get(file);
    if (!content) return { items: [] };

    const offset = lineCharToOffset(content, line, character);
    const completions = service.getCompletionsAtPosition(virtualPath, offset, {
        includeCompletionsForModuleExports: true,
        includeCompletionsWithInsertText: true,
    });

    if (!completions) return { items: [] };

    const items = completions.entries.slice(0, 100).map(entry => ({
        label: entry.name,
        kind: entry.kind,
        sortText: entry.sortText,
        insertText: entry.insertText || entry.name,
        isRecommended: entry.isRecommended || false,
    }));

    return { items };
}

function handleQuickInfo(params) {
    const { file, line, character } = params;
    const virtualPath = toVirtualPath(file);
    const content = virtualFiles.get(file);
    if (!content) return null;

    const offset = lineCharToOffset(content, line, character);
    const info = service.getQuickInfoAtPosition(virtualPath, offset);
    if (!info) return null;

    const displayParts = info.displayParts?.map(p => p.text).join('') ?? '';
    const documentation = info.documentation?.map(p => p.text).join('\n') ?? '';

    return {
        display: displayParts,
        documentation,
        kind: info.kind,
    };
}

function handleDefinition(params) {
    const { file, line, character } = params;
    const virtualPath = toVirtualPath(file);
    const content = virtualFiles.get(file);
    if (!content) return [];

    const offset = lineCharToOffset(content, line, character);
    const definitions = service.getDefinitionAtPosition(virtualPath, offset);
    if (!definitions) return [];

    return definitions.map(def => {
        const defFile = fromVirtualPath(def.fileName);
        const defContent = virtualFiles.get(defFile) || (() => {
            try { return readFileSync(def.fileName, 'utf-8'); }
            catch { return ''; }
        })();
        const start = offsetToLineChar(defContent, def.textSpan.start);
        return {
            file: defFile,
            line: start.line,
            character: start.character,
        };
    });
}

// ---------------------------------------------------------------------------
// Main loop: NDJSON over stdin/stdout
// ---------------------------------------------------------------------------

const rl = createInterface({ input: process.stdin });

rl.on('line', (line) => {
    let request;
    try {
        request = JSON.parse(line);
    } catch {
        return; // Skip malformed input
    }

    const { id, method, params } = request;
    let result = null;
    let error = null;

    try {
        switch (method) {
            case 'update':
                result = handleUpdate(params);
                break;
            case 'remove':
                result = handleRemove(params);
                break;
            case 'completions':
                result = handleCompletions(params);
                break;
            case 'quickInfo':
                result = handleQuickInfo(params);
                break;
            case 'definition':
                result = handleDefinition(params);
                break;
            default:
                error = `Unknown method: ${method}`;
        }
    } catch (e) {
        error = e.message || String(e);
    }

    const response = error ? { id, error } : { id, result };
    process.stdout.write(JSON.stringify(response) + '\n');
});

// Signal readiness
process.stdout.write(JSON.stringify({ id: 0, result: { ready: true } }) + '\n');
