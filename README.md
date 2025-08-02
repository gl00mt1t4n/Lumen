# Lumen - Token Analysis Platform

A professional, production-ready platform for analyzing cryptocurrency tokens using multiple data sources with automated processing pipelines.

## Features

- **Token Analysis**: Process and evaluate tokens using BullX, GMGN, and Defined.fi
- **Trader Evaluation**: Advanced filtering and risk assessment
- **Automated Processing**: Daily automated token processing pipeline
- **Real-time Dashboard**: Professional React frontend with dark theme
- **PostgreSQL Database**: Local PostgreSQL with Docker for development
- **Production Ready**: Clean architecture, proper error handling

## Architecture

```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   Frontend      │    │    Backend      │    │   Database      │
│   (React)       │◄──►│   (NestJS)      │◄──►│  (Postgre local)│      
│   Port: 3001    │    │   Port: 3000    │    │   File: data/   │
└─────────────────┘    └─────────────────┘    └─────────────────┘
```

## Tech Stack

### Backend
- **Framework**: NestJS with TypeScript
- **Database**: PostgreSQL with TypeORM
- **External APIs**: BullX, GMGN, Helius
- **Testing**: Jest with Supertest

### Frontend
- **Framework**: Next.js with TypeScript
- **Styling**: Tailwind CSS with dark theme

## Quick Start

### Prerequisites
- Node.js 18+
- Git

### 1. Clone and Setup
```bash
git clone <repository-url>
cd lumen
cp env.example .env
# Edit .env with your API keys
```

### 2. Start Development Environment
```bash
# Install dependencies
npm install

# Start Docker containers
npm run docker:up

# Start development servers
npm run dev
```

### 3. Access Applications
- **Frontend**: http://localhost:3001
- **Backend API**: http://localhost:3000
- **API Documentation**: http://localhost:3000/api/docs

## Project Structure

```
lumen/
├── src/
│   ├── backend/                    # NestJS Backend
│   │   └── src/                   # Backend source code
│   └── frontend/                  # React Frontend
│       └── src/                   # Frontend source code
├── data/                          # Database initialization
├── logs/                          # Application logs
├── package.json                   # Single package.json
├── tsconfig.json                  # TypeScript configuration

```

## Configuration

### Environment Variables
Copy `env.example` to `.env` and configure:

```bash
# Database
DATABASE_HOST=localhost
DATABASE_PORT=5432
DATABASE_NAME=lumen_dev
DATABASE_USER=lumen_user
DATABASE_PASSWORD=lumen_password

# External APIs
BULLX_HEADERS_JSON={...}
GMGN_HEADERS_JSON={...}
HELIUS_API_KEY=your_key

# Processing
OMNI_CONCURRENCY=7
DAILY_PROCESSING_SCHEDULE=0 2 * * *
```

### API Keys Setup
1. **BullX**: Get headers from browser DevTools
2. **GMGN**: Get headers from browser DevTools  
3. **Helius**: Sign up at https://helius.xyz

## Database

### Local Development
```bash
# Database is automatically created
# SQLite file: ./data/lumen.db
```

### Production
- Uses Vercel Postgres
- Automatic migrations on deployment
- Backup and monitoring included

## Processing Pipeline

### Daily Automated Processing
```typescript
// Runs daily at 2 AM
@Cron('0 2 * * *')
async handleDailyProcessing() {
  // 1. Fetch new tokens
  // 2. Process each token
  // 3. Evaluate traders
  // 4. Update analytics
  // 5. Send notifications
}
```

### Manual Processing
```bash
# Process specific token
curl -X POST http://localhost:3000/api/tokens/process \
  -H "Content-Type: application/json" \
  -d '{"tokenAddress": "0x..."}'

# Get processing status
curl http://localhost:3000/api/processing/status
```

## Testing

```bash
# Run all tests
npm test

# Backend tests only
npm run test:backend

# Frontend tests only
npm run test:frontend

# E2E tests
npm run test:e2e
```

## Deployment

### Development
```bash
npm run dev
```

### Production
```bash
# Build for production
npm run build

# Deploy to Vercel
vercel --prod
```

## API Endpoints

### Health & Status
- `GET /` - API status
- `GET /health` - Health check
- `GET /api/stats` - System statistics

### Tokens
- `GET /api/tokens` - List tokens
- `POST /api/tokens` - Add new token
- `GET /api/tokens/:id` - Get token details
- `POST /api/tokens/:id/process` - Process token

### Traders
- `GET /api/traders` - List traders
- `GET /api/traders/:id` - Get trader details
- `GET /api/traders/:id/evaluation` - Get evaluation

### Processing
- `GET /api/processing/status` - Processing status
- `POST /api/processing/start` - Start processing
- `DELETE /api/processing/stop` - Stop processing

## Frontend Features

- **Dark Theme**: Professional dark UI
- **Real-time Updates**: Live processing status
- **Data Visualization**: Charts and analytics
- **Responsive Design**: Mobile-friendly
- **Loading States**: Smooth user experience

## Security

- **Rate Limiting**: API request throttling
- **CORS**: Cross-origin resource sharing
- **Helmet**: Security headers
- **Input Validation**: Request validation
- **Error Handling**: Secure error responses

## Monitoring

- **Health Checks**: Application health monitoring
- **Logging**: Winston-based logging
- **Metrics**: Performance monitoring
- **Alerts**: Error notifications

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests
5. Submit a pull request

## License

MIT License - see LICENSE file for details

## Support

- **Issues**: GitHub Issues
- **Documentation**: `/docs` folder
- **API Docs**: `/api/docs` endpoint

---

**Built with NestJS, React, and SQLite**
