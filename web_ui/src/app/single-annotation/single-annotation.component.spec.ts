import { ComponentFixture, TestBed } from '@angular/core/testing';
import { SingleAnnotationComponent } from './single-annotation.component';
import { provideRouter } from '@angular/router';
import { JobsService } from '../job-creation/jobs.service';
import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { SingleAnnotationService } from '../single-annotation.service';
import { BehaviorSubject, Observable, of, throwError } from 'rxjs';
import { Annotator, AnnotatorDetails, Resource, SingleAnnotationReport, Annotatable } from '../single-annotation';
import { UserData, UsersService } from '../users.service';
import { MatTooltip } from '@angular/material/tooltip';
import { AnnotationPipelineStateService } from '../annotation-pipeline/annotation-pipeline-state.service';

const mockReport = new SingleAnnotationReport(
  new Annotatable('chr14', 204000100, 'A', 'AA', 'ins', null, null),
  [
    new Annotator(new AnnotatorDetails('allele_score', 'desc', [new Resource('resourceId', 'resourceUrl')]), [])
  ],
);
class MockSingleAnnotationService {
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  public getReport(annotatable: Annotatable, pipeline: string): Observable<SingleAnnotationReport> {
    return of(mockReport);
  }
}

const mockUser: UserData = {
  email: 'mockEmail',
  loggedIn: true,
  isAdmin: false,
  limitations: {
    dailyJobs: 10,
    filesize: '10MB',
    todayJobsCount: 10,
    variantCount: 20,
    diskSpace: '100'
  }
};
class MockUsersService {
  public userData = new BehaviorSubject<UserData>(mockUser);
}

describe('SingleAnnotationComponent', () => {
  let component: SingleAnnotationComponent;
  let fixture: ComponentFixture<SingleAnnotationComponent>;
  let pipelineStateService: AnnotationPipelineStateService;
  const mockSingleAnnotationService = new MockSingleAnnotationService();
  const mockUsersService = new MockUsersService();


  beforeEach(async() => {
    await TestBed.configureTestingModule({
      imports: [SingleAnnotationComponent],
      providers: [
        {
          provide: SingleAnnotationService,
          useValue: mockSingleAnnotationService
        },
        {
          provide: UsersService,
          useValue: mockUsersService
        },
        provideRouter([]),
        JobsService,
        MatTooltip,
        provideHttpClient(),
        provideHttpClientTesting(),
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(SingleAnnotationComponent);

    pipelineStateService = TestBed.inject(AnnotationPipelineStateService);
    pipelineStateService.selectedPipelineId.set('pipelineId');
    pipelineStateService.isConfigValid.set(true);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  it('should create', () => {
    expect(component).toBeTruthy();
  });

  it('should not show error message for valid alleles', () => {
    component.alleleInput.setValue('chr1 11796321 G A');
    component.annotateAllele();
    expect(component.alleleInput.valid).toBe(true);

    component.alleleInput.setValue('chr1 100 GTT A');
    component.annotateAllele();
    expect(component.alleleInput.valid).toBe(true);

    component.alleleInput.setValue('  chr1 11796321 G A ');
    component.annotateAllele();
    expect(component.alleleInput.valid).toBe(true);

    component.alleleInput.setValue('chr1:11796321:G:A');
    component.annotateAllele();
    expect(component.alleleInput.valid).toBe(true);

    component.alleleInput.setValue('chr1:11796321');
    component.annotateAllele();
    expect(component.alleleInput.valid).toBe(true);

    component.alleleInput.setValue('chr1 11796321');
    component.annotateAllele();
    expect(component.alleleInput.valid).toBe(true);

    component.alleleInput.setValue('chr1:11796321:G>A');
    component.annotateAllele();
    expect(component.alleleInput.valid).toBe(true);

    component.alleleInput.setValue('chr1:11796321-11800000');
    component.annotateAllele();
    expect(component.alleleInput.valid).toBe(true);

    component.alleleInput.setValue('chr1 11796321 11800000');
    component.annotateAllele();
    expect(component.alleleInput.valid).toBe(true);

    component.alleleInput.setValue('chr1:11796321 G A');
    component.annotateAllele();
    expect(component.alleleInput.valid).toBe(true);

    component.alleleInput.setValue('chr1\t11796321\tG\tA');
    component.annotateAllele();
    expect(component.alleleInput.valid).toBe(true);

    component.alleleInput.setValue('chr1\t11796321\t\tG A');
    component.annotateAllele();
    expect(component.alleleInput.valid).toBe(true);

    component.alleleInput.setValue('chr7 1     GTT A');
    component.annotateAllele();
    expect(component.alleleInput.valid).toBe(true);

    component.alleleInput.setValue('chr1:11796321:G::A');
    component.annotateAllele();
    expect(component.alleleInput.valid).toBe(true);

    component.alleleInput.setValue('chr1  11796321      G   ::A');
    component.annotateAllele();
    expect(component.alleleInput.valid).toBe(true);
  });

  it('should show error message for invalid alleles', () => {
    component.alleleInput.setValue('chr1 GTT A');
    component.annotateAllele();
    expect(component.alleleInput.valid).toBe(false);

    component.alleleInput.setValue('chr1:aaaa');
    component.annotateAllele();
    expect(component.alleleInput.valid).toBe(false);

    component.alleleInput.setValue('chr1 11796321aaa');
    component.annotateAllele();
    expect(component.alleleInput.valid).toBe(false);

    component.alleleInput.setValue('chr1 11796321 Gav\'>\'A');
    component.annotateAllele();
    expect(component.alleleInput.valid).toBe(false);

    component.alleleInput.setValue('chr1:-11796321');
    component.annotateAllele();
    expect(component.alleleInput.valid).toBe(false);

    component.alleleInput.setValue('chr1:11796321-111');
    component.annotateAllele();
    expect(component.alleleInput.valid).toBe(false);

    component.alleleInput.setValue('chr1:11796321:G > A');
    component.annotateAllele();
    expect(component.alleleInput.valid).toBe(false);

    component.alleleInput.setValue('chr1 11796321 G\'>\'A');
    component.annotateAllele();
    expect(component.alleleInput.valid).toBe(false);

    component.alleleInput.setValue('chr1:11796321:G\'>\'A');
    component.annotateAllele();
    expect(component.alleleInput.valid).toBe(false);

    component.alleleInput.setValue('chr1:11796321--11800000');
    component.annotateAllele();
    expect(component.alleleInput.valid).toBe(false);
  });

  it('should check if position of an allele is valid', () => {
    component.alleleInput.setValue('chr1 11796321 G A');
    component.annotateAllele();
    expect(component.alleleInput.valid).toBe(true);

    component.alleleInput.setValue('chr1 11,796,321 G A');
    component.annotateAllele();
    expect(component.alleleInput.valid).toBe(true);

    component.alleleInput.setValue('chr1 11,796,321 G A');
    component.annotateAllele();
    expect(component.alleleInput.valid).toBe(true);

    component.alleleInput.setValue('chr1:796,321-11,800,000');
    component.annotateAllele();
    expect(component.alleleInput.valid).toBe(true);

    component.alleleInput.setValue('chr1 11796321 11796321');
    component.annotateAllele();
    expect(component.alleleInput.valid).toBe(true);

    component.alleleInput.setValue('chr1 117,963,21 11,800,000');
    component.annotateAllele();
    expect(component.alleleInput.valid).toBe(false);

    component.alleleInput.setValue('chr1 11,,796,321 G A');
    component.annotateAllele();
    expect(component.alleleInput.valid).toBe(false);

    component.alleleInput.setValue('chr1 11,0,796,321 G A');
    component.annotateAllele();
    expect(component.alleleInput.valid).toBe(false);

    component.alleleInput.setValue('chr1 ,796,321 G A');
    component.annotateAllele();
    expect(component.alleleInput.valid).toBe(false);

    component.alleleInput.setValue('chr1 pos:11796321 G A');
    component.annotateAllele();
    expect(component.alleleInput.valid).toBe(false);
  });

  it('should check if reference of an allele is valid', () => {
    component.alleleInput.setValue('chr1 11796321 G A');
    component.annotateAllele();
    expect(component.alleleInput.valid).toBe(true);

    component.alleleInput.setValue('chr1 11796321 GT A');
    component.annotateAllele();
    expect(component.alleleInput.valid).toBe(true);

    component.alleleInput.setValue('chr1 11796321 ZZ A');
    component.annotateAllele();
    expect(component.alleleInput.valid).toBe(false);

    component.alleleInput.setValue('chr1 11796321 GT,N A');
    component.annotateAllele();
    expect(component.alleleInput.valid).toBe(false);

    component.alleleInput.setValue('chr1 11796321 aaa A');
    component.annotateAllele();
    expect(component.alleleInput.valid).toBe(true);

    component.alleleInput.setValue('chr1 11796321  A');
    component.annotateAllele();
    expect(component.alleleInput.valid).toBe(false);
  });

  it('should check if alternative of an allele is valid', () => {
    component.alleleInput.setValue('chr1 11796321 G A');
    component.annotateAllele();
    expect(component.alleleInput.valid).toBe(true);

    component.alleleInput.setValue('chr1 11796321 G GT');
    component.annotateAllele();
    expect(component.alleleInput.valid).toBe(true);

    component.alleleInput.setValue('chr1 11796321 G GT,N');
    component.annotateAllele();
    expect(component.alleleInput.valid).toBe(false);

    component.alleleInput.setValue('chr1 11796321 G gt,a');
    component.annotateAllele();
    expect(component.alleleInput.valid).toBe(false);

    component.alleleInput.setValue('chr1 11796321 G A,NN,NNP');
    component.annotateAllele();
    expect(component.alleleInput.valid).toBe(false);

    component.alleleInput.setValue('chr1 11796321 G  ');
    component.annotateAllele();
    expect(component.alleleInput.valid).toBe(false);
  });

  it('should get report when clicking go button and input is valid', () => {
    component.alleleInput.setValue('chr1 11796321 G GT');
    const getReportSpy = jest.spyOn(mockSingleAnnotationService, 'getReport');

    component.annotateAllele();
    expect(component.report).toBe(mockReport);
    expect(getReportSpy).toHaveBeenCalledWith(new Annotatable(
      'chr1',
      11796321,
      'G',
      'GT',
      null,
      null,
      null
    ), 'pipelineId');
  });

  it('should set report to null when input is not valid', () => {
    component.alleleInput.setValue('chr1 11796321 G NNP');
    component.annotateAllele();
    expect(component.report).toBeNull();
  });

  it('should trigger update table in parent after getting the report', () => {
    component.alleleInput.setValue('chr1 11796321 G GT');
    const emitSpy = jest.spyOn(component.alleleUpdateEmit, 'emit');

    component.annotateAllele();
    expect(emitSpy).toHaveBeenCalledWith();
  });

  it('should not trigger update table in parent after getting the report when user is anonymous', () => {
    component.alleleInput.setValue('chr1 11796321 G GT');
    const emitSpy = jest.spyOn(component.alleleUpdateEmit, 'emit');

    mockUsersService.userData = null;

    component.annotateAllele();
    expect(emitSpy).not.toHaveBeenCalledWith();
  });

  it('should disable Go button when no pipeline is selected', () => {
    pipelineStateService.selectedPipelineId.set('');
    pipelineStateService.currentTemporaryPipelineId.set('');
    component.alleleInput.setValue('chr1 11796321 G A');
    expect(component.disableGo()).toBe(true);
  });

  it('should disable Go button when pipeline config is invalid', () => {
    pipelineStateService.isConfigValid.set(false);
    component.alleleInput.setValue('chr1 11796321 G A');
    expect(component.disableGo()).toBe(true);
  });

  it('should disable Go button when allele input is empty', () => {
    component.alleleInput.setValue('');
    expect(component.disableGo()).toBe(true);
  });

  it('should set allele input value and reset report', () => {
    component.report = mockReport;
    component.setAllele('chr1 11796321 G A');
    expect(component.alleleInput.value).toBe('chr1 11796321 G A');
    expect(component.report).toBeNull();
  });

  it('should emit autoSaveTrigger when triggering pipeline auto save', () => {
    const emitSpy = jest.spyOn(component.autoSaveTrigger, 'emit');
    component.triggerPipelineAutoSave();
    expect(emitSpy).toHaveBeenCalledWith();
  });

  it('should set loading to false and keep report null when annotation fails', () => {
    jest.spyOn(mockSingleAnnotationService, 'getReport').mockReturnValueOnce(throwError(new Error('error')));
    component.alleleInput.setValue('chr1 11796321 G A');
    component.annotateAllele();
    expect(component.loading).toBe(false);
    expect(component.report).toBeNull();
  });
});
