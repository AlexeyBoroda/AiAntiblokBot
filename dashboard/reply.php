<?php
header('Content-Type: application/json; charset=utf-8');
require_once __DIR__ . '/config.php';

function fail($msg){ echo json_encode(['ok'=>false,'error'=>$msg], JSON_UNESCAPED_UNICODE); exit; }

$token = isset($_POST['token']) ? trim($_POST['token']) : '';
if ($token === '' || $token !== DASH_TOKEN) fail('bad token');

$answer_id = isset($_POST['answer_id']) ? trim($_POST['answer_id']) : '';
$user_id = isset($_POST['user_id']) ? trim($_POST['user_id']) : '';
$text = isset($_POST['text']) ? trim($_POST['text']) : '';

if ($answer_id === '' || $user_id === '' || $text === '') fail('missing fields');

$evt = ['ts'=>time(),'type'=>'admin_reply','answer_id'=>$answer_id,'user_id'=>intval($user_id),'text'=>$text];

$outbox = realpath(__DIR__ . '/../data') . '/outbox.jsonl';
$line = json_encode($evt, JSON_UNESCAPED_UNICODE) . "\n";

if (@file_put_contents($outbox, $line, FILE_APPEND | LOCK_EX) === false) fail('cannot write outbox');

echo json_encode(['ok'=>true], JSON_UNESCAPED_UNICODE);
