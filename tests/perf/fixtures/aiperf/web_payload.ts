import { buildAIPerfPayload, DEFAULT_AIPERF } from '../../../../evalscope/web/src/components/tasks/aiperfDraft'
import { DEFAULT_CONNECTION } from '../../../../evalscope/web/src/components/tasks/taskConfig'

const [url, tokenizer] = process.argv.slice(2)
const draft = { ...DEFAULT_AIPERF, tokenizer, ratios: '0, 80, 90', inputTokens: '40', requestCount: '2',
  outputTokens: '2', loads: '1', repetitions: '1', pool: '1', seed: '42' }
const connection = { ...DEFAULT_CONNECTION, model: 'mock-model', url, apiKey: 'fixture-key' }
process.stdout.write(JSON.stringify(buildAIPerfPayload(draft, connection)))
