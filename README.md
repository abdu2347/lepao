# 乐跑数据生成器

## 部署到 Railway.app（推荐，免费）

1. 把这个文件夹上传到 GitHub 仓库
2. 注册 https://railway.app （GitHub登录）
3. 点 "New Project" → "Deploy from GitHub repo"
4. 选择你的仓库
5. 自动部署完成

## 部署到 Vercel（需改数据库）

Vercel 不支持 SQLite 写入，如需部署到 Vercel 请联系开发者适配。

## 本地运行

```bash
pip install -r requirements.txt
python app.py
```

访问 http://127.0.0.1:5000

## 管理员

- 地址: /admin
- 默认账号: admin / admin123
