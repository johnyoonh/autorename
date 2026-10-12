"""Exercise the real SDK request and parser against synthetic HTTP responses."""
import json
from unittest.mock import patch

import httpx
from openai import OpenAI, AuthenticationError
from PIL import Image
import pytest

import _ai_processing as ai


def _response(content=None, status='completed'):
    return {'id': 'resp_test', 'object': 'response', 'created_at': 0, 'model': 'synthetic-model',
            'status': status, 'error': None, 'incomplete_details': None,
            'output': [{'id': 'msg_test', 'type': 'message', 'role': 'assistant', 'status': 'completed',
                        'content': content if content is not None else [{'type': 'output_text', 'text': json.dumps({'company_name': 'ACME', 'document_date': '15.03.2024', 'document_type': 'Invoice'}), 'annotations': []}]}]}


@pytest.mark.parametrize('mode', ['text', 'image', 'mixed'])
def test_openai_uses_responses_and_parses_schema(sample_config, mode):
    requests = []
    def handle(request):
        requests.append(request)
        return httpx.Response(200, json=_response())
    client = OpenAI(api_key='synthetic', http_client=httpx.Client(transport=httpx.MockTransport(handle)))
    image = Image.new('RGB', (20, 20))
    with patch.object(ai, 'OpenAI', return_value=client):
        if mode == 'text':
            result = ai.extract_metadata_from_text('Synthetic invoice', sample_config)
        elif mode == 'image':
            result = ai.extract_metadata_from_images([image], sample_config)
        else:
            result = ai.extract_metadata_from_text_and_images('Synthetic invoice', [image], sample_config)
    assert result.company_name == 'ACME'
    assert len(requests) == 1
    assert requests[0].url.path == '/v1/responses'
    payload = json.loads(requests[0].content)
    assert payload['model'] == sample_config['ai']['model']
    assert payload['store'] is False
    assert 'temperature' not in payload  # supported by reasoning models without sampling overrides
    assert payload['text']['format']['strict'] is True
    assert set(payload['text']['format']['schema']['required']) == {'company_name', 'document_date', 'document_type'}
    content = payload['input'][0]['content']
    types = [item['type'] for item in content]
    assert ('input_image' in types) == (mode != 'text')
    assert 'input_text' in types
    if mode != 'text':
        assert content[-1]['image_url'].startswith('data:image/png;base64,')


@pytest.mark.parametrize('content,status', [([{'type': 'refusal', 'refusal': 'synthetic refusal'}], 'completed'), ([], 'incomplete')])
def test_missing_metadata_is_an_error(sample_config, content, status):
    client = OpenAI(api_key='synthetic', http_client=httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=_response(content, status)))))
    with patch.object(ai, 'OpenAI', return_value=client), pytest.raises(RuntimeError, match='structured document metadata'):
        ai.extract_metadata_from_text('Synthetic invoice', sample_config)


def test_openai_auth_error_is_not_retried(sample_config):
    requests = []
    def handle(request):
        requests.append(request)
        return httpx.Response(401, json={'error': {'message': 'Missing scopes: model.request', 'type': 'invalid_request_error', 'code': 'missing_scope'}})
    client = OpenAI(api_key='synthetic', max_retries=2, http_client=httpx.Client(transport=httpx.MockTransport(handle)))
    with patch.object(ai, 'OpenAI', return_value=client), pytest.raises(AuthenticationError):
        ai.extract_metadata_from_text('Synthetic invoice', sample_config)
    assert len(requests) == 1
