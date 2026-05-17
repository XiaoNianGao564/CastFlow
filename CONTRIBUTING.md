# Contributing to CastFlow

Thanks for your interest in contributing! This is a brief guide to get you started.

## Development Setup

1. **Fork & Clone**
   ```bash
   git clone https://github.com/your-username/CastFlow.git
   cd CastFlow
   ```

2. **Install Dependencies**
   ```bash
   bun install
   ```

3. **Configure Environment**
   ```bash
   echo "sk-your-api-key" > .api_key
   ```

4. **Start Development**
   ```bash
   bun run dev:api    # API on :3003
   bun run dev:app    # Frontend on :3000
   ```

## Project Structure

```
packages/
  core/    # Agent system, types, stores
  api/     # Hono HTTP server
  app/     # SolidJS frontend
```

## Commit Convention

Follow [Conventional Commits](https://www.conventionalcommits.org/):

- `feat:` new feature
- `fix:` bug fix
- `docs:` documentation
- `refactor:` code restructuring
- `chore:` maintenance

## Pull Request Process

1. Create a feature branch from `main`
2. Make your changes with clear commits
3. Open a PR against `main` with a description of changes
4. Ensure the build passes

## Questions?

Feel free to open an issue for any questions or discussion.
