echo "== 1. teach =="
curl -s -X POST http://127.0.0.1:3000/Add --data-urlencode "ip=127.0.0.1" --data-urlencode "keyword=你好" --data-urlencode "answer=你好呀,我是白丝魔理沙!"
echo
echo "== 2. teach 2nd =="
curl -s -X POST http://127.0.0.1:3000/Add --data-urlencode "ip=127.0.0.1" --data-urlencode "keyword=今天天气怎么样" --data-urlencode "answer=今天也是个好天气呢~"
echo
echo "== 3. status =="
curl -s -X POST http://127.0.0.1:3000/Status
echo
echo "== 4. reply 你好 =="
curl -s -X POST http://127.0.0.1:3000/Reply --data-urlencode "keyword=你好"
echo
echo "== 5. reply 今天天气怎么样 =="
curl -s -X POST http://127.0.0.1:3000/Reply --data-urlencode "keyword=今天天气怎么样"
echo
echo "== 6. reply 未教的 =="
curl -s -X POST http://127.0.0.1:3000/Reply --data-urlencode "keyword=量子力学"
echo
echo "== 7. forget =="
curl -s -X POST http://127.0.0.1:3000/Forget --data-urlencode "answer=你好呀,我是白丝魔理沙!"
echo
echo "== 8. reply after forget =="
curl -s -X POST http://127.0.0.1:3000/Reply --data-urlencode "keyword=你好"
