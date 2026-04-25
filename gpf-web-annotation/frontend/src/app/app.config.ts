import { ApplicationConfig, provideZoneChangeDetection } from '@angular/core';
import { provideRouter } from '@angular/router';

import { routes } from './app.routes';
import { provideHttpClient } from '@angular/common/http';
import { UsersService } from './users.service';
import { JobsService } from './job-creation/jobs.service';
import { SingleAnnotationService } from './single-annotation.service';
import { provideMarkdown } from 'ngx-markdown';
import { AnnotationPipelineService } from './annotation-pipeline.service';
import { provideMonacoEditor } from 'ngx-monaco-editor-v2';
import { AboutPageService } from './about-page/about-page.service';

export const appConfig: ApplicationConfig = {
  providers: [
    provideZoneChangeDetection({ eventCoalescing: true }),
    provideRouter(routes),
    provideHttpClient(),
    UsersService,
    JobsService,
    SingleAnnotationService,
    AnnotationPipelineService,
    AboutPageService,
    provideMarkdown(),
    provideMonacoEditor()
  ]
};
