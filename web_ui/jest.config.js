var esModules = ['d3', 'd3-array', 'internmap', 'delaunator', 'robust-predicates', 'marked'].join('|');

module.exports = {
  preset: "jest-preset-angular",
  globalSetup: "jest-preset-angular/global-setup",
  setupFilesAfterEnv: ["<rootDir>/setup-jest.ts"],
  modulePaths: ["<rootDir>/src"],
  transformIgnorePatterns: ['node_modules/(?!' + esModules + '|.*.mjs$)'],
  reporters: [
    "default",
    [
      "jest-junit",
      { outputDirectory: "test-reports", outputName: "frontend-junit-report.xml" },
    ],
  ],
  collectCoverage: true,
  coverageDirectory: "<rootDir>/coverage",
  coverageReporters: ["html", "cobertura"],
};
