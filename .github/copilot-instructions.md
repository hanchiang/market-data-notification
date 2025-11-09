# Universal Software Engineering Best Practices

This document outlines comprehensive best practices for software engineering that apply across all programming languages, frameworks, and project types.

## 🏗️ Code Architecture & Design

### SOLID Principles
- **Single Responsibility**: Each class/function should have one reason to change
- **Open/Closed**: Open for extension, closed for modification
- **Liskov Substitution**: Subtypes must be substitutable for their base types
- **Interface Segregation**: Clients shouldn't depend on interfaces they don't use
- **Dependency Inversion**: Depend on abstractions, not concretions

### Design Patterns
- Use appropriate design patterns (Factory, Observer, Strategy, etc.) when they solve real problems
- Avoid over-engineering with unnecessary patterns
- Document pattern usage and rationale in comments

## 📝 Code Quality & Standards

### Naming Conventions
- Use clear, descriptive, and meaningful names for variables, functions, and classes
- Avoid abbreviations and acronyms unless they're widely understood
- Use consistent naming patterns throughout the codebase
- Boolean variables should be clearly identifiable (e.g., `isActive`, `hasPermission`)

### Code Structure
- Keep functions small and focused (ideally < 20 lines)
- Limit function parameters (max 3-4, use objects for more)
- Use early returns to reduce nesting
- Group related functionality into modules/classes
- Maintain consistent indentation and formatting

### Comments & Documentation
- Write self-documenting code that explains the "what"
- Use comments to explain the "why" and complex business logic
- Keep comments up-to-date with code changes
- Document public APIs, interfaces, and complex algorithms
- Use TODO comments sparingly and track them

### Documentation Format
- **Always output raw Markdown files** for code reviews, planning documents, and technical documentation
- Use proper Markdown syntax for headers, lists, code blocks, and formatting
- Output should be pure Markdown content without additional formatting or wrapper text
- Ensure documents are easily modifiable and portable across different platforms and tools
- Structure content with clear headings and consistent formatting for readability

## 🔒 Error Handling & Validation

### Error Management
- Use appropriate error handling mechanisms (try-catch, Result types, etc.)
- Fail fast with meaningful error messages
- Log errors with sufficient context for debugging
- Handle errors at the appropriate level of abstraction
- Avoid swallowing exceptions silently

### Input Validation
- Validate all inputs at system boundaries
- Use type systems and static analysis tools
- Sanitize user inputs to prevent security vulnerabilities
- Provide clear error messages for invalid inputs

## 🧪 Testing Strategy

### Test Coverage
- Aim for high test coverage (80%+) but focus on quality over quantity
- Write unit tests for business logic
- Include integration tests for system interactions
- Add end-to-end tests for critical user flows

### Test Quality
- Follow the AAA pattern (Arrange, Act, Assert)
- Use descriptive test names that explain the scenario
- Keep tests isolated and independent
- Mock external dependencies appropriately
- Test both happy paths and edge cases

## 🚀 Performance & Optimization

### General Guidelines
- Optimize for readability first, performance second
- Measure before optimizing (use profiling tools)
- Consider algorithmic complexity (Big O notation)
- Avoid premature optimization
- Cache expensive operations when appropriate

### Resource Management
- Close resources properly (files, connections, etc.)
- Be mindful of memory usage and potential leaks
- Use appropriate data structures for the use case
- Consider lazy loading for expensive operations

## 🔐 Security Best Practices

### Data Protection
- Never store sensitive data in plaintext
- Use secure communication protocols (HTTPS, TLS)
- Implement proper authentication and authorization
- Follow the principle of least privilege
- Sanitize all inputs to prevent injection attacks

### Code Security
- Keep dependencies up-to-date
- Use static analysis security tools
- Avoid hardcoded secrets and credentials
- Implement proper session management
- Use secure coding practices for your language/framework

## 📚 Dependencies & Libraries

### Dependency Management
- Keep dependencies minimal and necessary
- Regularly update dependencies for security patches
- Audit dependencies for vulnerabilities
- Use lock files to ensure reproducible builds
- Document why each dependency is needed

### Library Selection
- Choose well-maintained, popular libraries
- Evaluate license compatibility
- Consider the library's performance impact
- Have exit strategies for critical dependencies

## 🔄 Version Control & Collaboration

### Git Best Practices
- Write clear, descriptive commit messages
- Make small, focused commits
- Use feature branches for development
- Review code before merging
- Keep the main branch stable and deployable

### Code Reviews
- Review for logic, style, security, and performance
- Be constructive and respectful in feedback
- Test the changes locally when possible
- Ensure documentation is updated
- Check for potential breaking changes

## 🚀 Deployment & DevOps

### Continuous Integration/Deployment
- Automate testing and deployment pipelines
- Use infrastructure as code when possible
- Implement proper monitoring and logging
- Have rollback strategies in place
- Use feature flags for gradual rollouts

### Environment Management
- Keep environments consistent (dev, staging, prod)
- Use configuration management for environment-specific settings
- Never deploy directly to production
- Maintain proper secrets management

## 📊 Monitoring & Maintenance

### Observability
- Implement comprehensive logging
- Use structured logging formats
- Set up monitoring and alerting
- Track key performance metrics
- Create meaningful dashboards

### Technical Debt
- Regularly refactor and improve code quality
- Document known technical debt
- Prioritize debt that impacts development velocity
- Set aside time for maintenance and improvements

## 🎯 Project-Specific Guidelines

### Adapt These Practices
- Tailor these guidelines to your specific project needs
- Consider team size and experience level
- Account for project timeline and requirements
- Balance perfection with pragmatism
- Regularly review and update these practices

### Team Alignment
- Ensure all team members understand these practices
- Provide training and resources as needed
- Use linting and automated tools to enforce standards
- Regular retrospectives to improve processes
- Document project-specific deviations and reasons

---

*Remember: These are guidelines, not rigid rules. Use good judgment and adapt them to your specific context and requirements.*
