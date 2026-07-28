import express from 'express';
import path from 'path';
import { fileURLToPath } from 'url';
import { GoogleGenAI } from '@google/genai';
import { createServer as createViteServer } from 'vite';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

async function startServer() {
  const app = express();
  const PORT = Number(process.env.PORT || 3000);

  app.use(express.json({ limit: '10mb' }));

  // Initialize Gemini lazily
  function getGeminiClient() {
    const apiKey = process.env.GEMINI_API_KEY;
    if (!apiKey) {
      return null;
    }
    return new GoogleGenAI({ apiKey });
  }

  // API Route: Health check
  app.get('/api/health', (req, res) => {
    res.json({ status: 'ok', time: new Date().toISOString() });
  });

  // API Route: AI Agent Plan Generation (Demand Planning Agent)
  app.post('/api/gemini/agent-plan', async (req, res) => {
    try {
      const { userRequirement, taskType, datasetType } = req.body;
      const ai = getGeminiClient();

      if (!ai) {
        return res.json({
          success: false,
          fallback: true,
          message: 'GEMINI_API_KEY is not configured. Falling back to local deterministic template.',
        });
      }

      const prompt = `You are DataAgent Demand Planning Agent. Analyze the following data production request and output a structured TaskSpec JSON.
Request: "${userRequirement}"
Task Type: "${taskType || 'Multimodal Model Training'}"
Dataset Input: "${datasetType || 'Image/Text Dataset'}"

Respond ONLY with a valid JSON object matching this structure:
{
  "taskName": "string",
  "hardConstraints": ["string"],
  "semanticConstraints": ["string"],
  "outputRequirements": ["string"],
  "acceptanceCriteria": ["string"],
  "ambiguities": ["string"],
  "recommendedCandidatePipelines": [
    {
      "type": "Retention-First | Balanced | Quality-First",
      "name": "string",
      "summary": "string"
    }
  ]
}`;

      const response = await ai.models.generateContent({
        model: 'gemini-2.5-flash',
        contents: prompt,
        config: {
          responseMimeType: 'application/json',
        },
      });

      const text = response.text || '';
      const parsed = JSON.parse(text);
      res.json({ success: true, data: parsed });
    } catch (err: any) {
      console.error('Gemini Agent Plan error:', err);
      res.json({
        success: false,
        error: err.message,
        message: 'AI planning request failed, falling back to local workflow generator.',
      });
    }
  });

  // API Route: AI Operator Assistant & Code Generation
  app.post('/api/gemini/operator-assist', async (req, res) => {
    try {
      const { prompt: userPrompt, operatorName, codeLanguage } = req.body;
      const ai = getGeminiClient();

      if (!ai) {
        return res.json({
          success: false,
          message: 'GEMINI_API_KEY missing.',
        });
      }

      const prompt = `You are a Senior Data Engine Architect for DataAgent.
Help generate code example, implementation specification, or parameter tuning guide for the Operator: "${operatorName || 'Custom Data Operator'}".
User Query: "${userPrompt}"
Target Language: "${codeLanguage || 'Python'}"

Provide a clean, production-ready code snippet with docstrings, input/output schemas, and usage examples.
Format in Markdown.`;

      const response = await ai.models.generateContent({
        model: 'gemini-2.5-flash',
        contents: prompt,
      });

      res.json({ success: true, markdown: response.text });
    } catch (err: any) {
      console.error('Gemini Operator Assist error:', err);
      res.status(500).json({ success: false, error: err.message });
    }
  });

  // Proxy DataAgent control-plane APIs to the Python backend. This keeps the
  // browser same-origin and avoids CORS setup during local development.
  app.use('/api', async (req, res, next) => {
    if (req.path.startsWith('/gemini') || req.path === '/health') {
      return next();
    }

    const backendBase = (process.env.DATAAGENT_API_BASE || 'http://127.0.0.1:8000').replace(/\/$/, '');
    const targetUrl = `${backendBase}/api${req.path}${req.url.includes('?') ? req.url.slice(req.url.indexOf('?')) : ''}`;

    try {
      const headers: Record<string, string> = {
        'content-type': String(req.headers['content-type'] || 'application/json'),
      };
      for (const key of ['x-owner-id', 'idempotency-key', 'accept']) {
        const value = req.headers[key];
        if (typeof value === 'string') headers[key] = value;
      }

      const response = await fetch(targetUrl, {
        method: req.method,
        headers,
        body: ['GET', 'HEAD'].includes(req.method)
          ? undefined
          : JSON.stringify(req.body || {}),
      });

      res.status(response.status);
      const contentType = response.headers.get('content-type');
      if (contentType) res.setHeader('content-type', contentType);

      const payload = Buffer.from(await response.arrayBuffer());
      res.send(payload);
    } catch (err: any) {
      res.status(502).json({
        detail: `DataAgent backend proxy failed: ${err.message}`,
      });
    }
  });

  // Vite middleware or production static build
  if (process.env.NODE_ENV !== 'production') {
    const vite = await createViteServer({
      server: { middlewareMode: true },
      appType: 'spa',
    });
    app.use(vite.middlewares);
  } else {
    const distPath = path.join(process.cwd(), 'dist');
    app.use(express.static(distPath));
    app.get('*', (req, res) => {
      res.sendFile(path.join(distPath, 'index.html'));
    });
  }

  app.listen(PORT, '0.0.0.0', () => {
    console.log(`DataAgent server running on http://0.0.0.0:${PORT}`);
  });
}

startServer();
