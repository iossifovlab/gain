import { ComponentFixture, TestBed } from '@angular/core/testing';

import { SingleAnnotationReportComponent } from './single-annotation-report.component';
import { BehaviorSubject } from 'rxjs';
import {
  Annotator,
  AnnotatorDetails,
  Attribute,
  Resource,
  Result,
  SingleAnnotationReport,
  Annotatable
} from '../single-annotation';
import { ActivatedRoute, provideRouter } from '@angular/router';
import { provideMarkdown } from 'ngx-markdown';
import { JobsService } from '../job-creation/jobs.service';
import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import FileSaver from 'file-saver';
import { MatDialog } from '@angular/material/dialog';
import { ViewportService } from '../viewport.service';
import { SingleAnnotationReportStateService } from './single-annotation-report-state.service';


describe('SingleAnnotationReportComponent', () => {
  let component: SingleAnnotationReportComponent;
  let fixture: ComponentFixture<SingleAnnotationReportComponent>;

  beforeEach(async() => {
    await TestBed.configureTestingModule({
      imports: [SingleAnnotationReportComponent],
      providers: [
        JobsService,
        provideHttpClient(),
        provideHttpClientTesting(),
        provideRouter([]),
        provideMarkdown()
      ]
    }).compileComponents();

    fixture = TestBed.createComponent(SingleAnnotationReportComponent);
    component = fixture.componentInstance;

    const activatedRoute = TestBed.inject(ActivatedRoute);
    (activatedRoute.queryParams as BehaviorSubject<{annotatable: string, pipeline: string}>).next({
      annotatable: 'chr14 204000100 A AA',
      pipeline: 'pipeline'
    });

    fixture.detectChanges();
  });

  it('should create', () => {
    expect(component).toBeTruthy();
  });

  it('should display true, false and 0 when there is no histogram', () => {
    const report = new SingleAnnotationReport(
      new Annotatable('chr14', 204000100, 'A', 'AA', 'ins', null, null),
      [
        new Annotator(new AnnotatorDetails('allele_score', 'desc', [new Resource('resourceId', 'resourceUrl')]), [
          new Attribute('attr1', 'desc1', 'AF', null, {value: 'true', histogramLink: null} as Result),
          new Attribute('attr2', 'desc2', 'AF', null, {value: 'false', histogramLink: null} as Result),
          new Attribute('attr3', 'desc3', 'AF', null, {value: 0, histogramLink: null} as Result),
        ])
      ],
    );

    component.report = report;
    component.showFullReport = true;
    fixture.detectChanges();

    const allValueElements = (fixture.nativeElement as HTMLElement).querySelectorAll('.value-result');
    expect(allValueElements).toHaveLength(3);
    expect(allValueElements[0].innerHTML).toBe('true');
    expect(allValueElements[1].innerHTML).toBe('false');
    expect(allValueElements[2].innerHTML).toBe('0');
  });

  it('should set sort state when sorting a new column', () => {
    const attribute = new Attribute('attr1', 'desc1', 'AF', null, new Result(
      new Map([['KeyA', 3], ['KeyB', 1], ['KeyC', 2]]), null
    ));

    component.sort('Value', attribute);

    expect(component.sortState.get(attribute)?.column).toBe('Value');
    expect(component.sortState.get(attribute)?.direction).toBe('asc');
  });

  it('should toggle sort direction when sorting the same column twice', () => {
    const attribute = new Attribute('attr1', 'desc1', 'AF', null, new Result(
      new Map([['KeyA', 3], ['KeyB', 1], ['KeyC', 2]]), null
    ));

    component.sort('Value', attribute);
    expect(component.sortState.get(attribute)?.direction).toBe('asc');

    component.sort('Value', attribute);
    expect(component.sortState.get(attribute)?.direction).toBe('desc');
  });

  it('should reset sort direction to asc when switching to a different column', () => {
    const attribute = new Attribute('attr1', 'desc1', 'AF', null, new Result(
      new Map([['KeyA', 3], ['KeyB', 1], ['KeyC', 2]]), null
    ));

    component.sort('Value', attribute);
    component.sort('Value', attribute);
    expect(component.sortState.get(attribute)?.direction).toBe('desc');

    component.sort('Key', attribute);
    expect(component.sortState.get(attribute)?.column).toBe('Key');
    expect(component.sortState.get(attribute)?.direction).toBe('asc');
  });

  it('should sort by key name ascending', () => {
    const attribute = new Attribute('attr1', 'desc1', 'AF', null, new Result(
      new Map([['KeyC', 2], ['KeyA', 3], ['KeyB', 1]]), null
    ));

    component.sort('Key', attribute);

    const keys = [...(attribute.result.value as Map<string, number>).keys()];
    expect(keys).toStrictEqual(['KeyA', 'KeyB', 'KeyC']);
  });

  it('should sort by key name descending', () => {
    const attribute = new Attribute('attr1', 'desc1', 'AF', null, new Result(
      new Map([['KeyC', 2], ['KeyA', 3], ['KeyB', 1]]), null
    ));

    component.sort('Key', attribute);
    component.sort('Key', attribute);

    const keys = [...(attribute.result.value as Map<string, number>).keys()];
    expect(keys).toStrictEqual(['KeyC', 'KeyB', 'KeyA']);
  });

  it('should sort by value ascending', () => {
    const attribute = new Attribute('attr1', 'desc1', 'AF', null, new Result(
      new Map([['KeyA', 3], ['KeyB', 1], ['KeyC', 2]]), null
    ));

    component.sort('Value', attribute);

    const entries = [...(attribute.result.value as Map<string, number>).entries()];
    expect(entries).toStrictEqual([['KeyB', 1], ['KeyC', 2], ['KeyA', 3]]);
  });

  it('should sort by value descending', () => {
    const attribute = new Attribute('attr1', 'desc1', 'AF', null, new Result(
      new Map([['KeyA', 3], ['KeyB', 1], ['KeyC', 2]]), null
    ));

    component.sort('Value', attribute);
    component.sort('Value', attribute);

    const entries = [...(attribute.result.value as Map<string, number>).entries()];
    expect(entries).toStrictEqual([['KeyA', 3], ['KeyC', 2], ['KeyB', 1]]);
  });

  it('should not sort when attribute value is not a Map', () => {
    const attribute = new Attribute('attr1', 'desc1', 'AF', null, new Result('plain string', null));

    component.sort('Value', attribute);

    expect(attribute.result.value).toBe('plain string');
  });

  it('should save report as file', async() => {
    const saveAsSpy = jest.spyOn(FileSaver, 'saveAs').mockImplementation(() => null);

    const report = new SingleAnnotationReport(
      new Annotatable('chr14', 204000100, 'A', 'AA', 'ins', null, null),
      [
        new Annotator(new AnnotatorDetails('allele_score', 'desc', [new Resource('resourceId', 'resourceUrl')]), [
          new Attribute('attr1', 'desc1\nblabla1\n', 'AF', null, {value: 'true', histogramLink: null} as Result),
          new Attribute('attr2', 'desc2\nblabla2\n', 'AF', null, {value: 13, histogramLink: null} as Result),
          new Attribute('attr3', 'desc3\nblabla3\n', 'AF', null, {value: 'mock_value', histogramLink: null} as Result),
          new Attribute('attr4', 'desc4\nblabla4\n', 'AF', null,
            {value: new Map<string, number>([['fo', 5], ['po', 3]]), histogramLink: null} as Result
          ),
        ])
      ],
    );

    component.report = report;
    component.saveReport();

    expect(saveAsSpy.mock.calls[0][1]).toBe('chr14_204000100_A_AA_report.tsv');
    const savedBlob = saveAsSpy.mock.calls[0][0] as Blob;

    const savedText = await savedBlob.text();
    const expectedText = 'Attribute name\tValue\tDescription\n'
      + 'chromosome\tchr14\n'
      + 'position\t204000100\n'
      + 'reference\tA\n'
      + 'alternative\tAA\n'
      + 'attr1\ttrue\tdesc1 blabla1\n'
      + 'attr2\t13\tdesc2 blabla2\n'
      + 'attr3\tmock_value\tdesc3 blabla3\n'
      +'attr4\tfo:5;po:3\tdesc4 blabla4\n';
    expect(savedText).toBe(expectedText);
  });

  it('should open dialog with desktop dimensions when not on mobile', () => {
    const dialog = TestBed.inject(MatDialog);
    const openSpy = jest.spyOn(dialog, 'open').mockImplementation(jest.fn());
    TestBed.inject(ViewportService).isMobile.set(false);

    const mockAnnotator = new Annotator(
      new AnnotatorDetails('score', 'desc', [new Resource('rid', 'rurl')]), []
    );
    component.showInfo(mockAnnotator);

    expect(openSpy).toHaveBeenCalledWith(
      component.infoModalRef,
      expect.objectContaining({
        data: mockAnnotator,
        width: '50vw',
        maxWidth: '1000px',
        minWidth: '500px',
        maxHeight: '700px',
      })
    );
  });

  it('should open dialog with mobile dimensions when on mobile', () => {
    const dialog = TestBed.inject(MatDialog);
    const openSpy = jest.spyOn(dialog, 'open').mockImplementation(jest.fn());
    TestBed.inject(ViewportService).isMobile.set(true);

    component.showInfo(new Annotator(new AnnotatorDetails('score', 'desc', []), []));

    expect(openSpy).toHaveBeenCalledWith(
      component.infoModalRef,
      expect.objectContaining({
        width: '95vw',
        maxWidth: '95vw',
        minWidth: 'unset',
        maxHeight: '70vh',
      })
    );
  });

  it('should flip isFullReport state when toggleView is called', () => {
    const stateService = TestBed.inject(SingleAnnotationReportStateService);

    component.toggleView();

    expect(stateService.isFullReport()).toBe(true);
  });

  it('should serialize array attribute value as JSON in saved report', async() => {
    const saveAsSpy = jest.spyOn(FileSaver, 'saveAs').mockImplementation(() => null);
    saveAsSpy.mockClear();
    const report = new SingleAnnotationReport(
      new Annotatable('chr1', 100, 'A', 'T', 'snv', null, null),
      [
        new Annotator(new AnnotatorDetails('score', 'desc', [new Resource('rid', 'rurl')]), [
          new Attribute('effects', 'effect list', 'AF', null,
            {value: ['missense', 'synonymous'], histogramLink: null} as Result),
        ]),
      ],
    );

    component.report = report;
    component.saveReport();

    const savedText = await (saveAsSpy.mock.calls[0][0] as Blob).text();
    expect(savedText).toContain('effects\tmissense;synonymous\teffect list\n');
  });

  it('should write empty string for null attribute value in saved report', async() => {
    const saveAsSpy = jest.spyOn(FileSaver, 'saveAs').mockImplementation(() => null);
    saveAsSpy.mockClear();
    const report = new SingleAnnotationReport(
      new Annotatable('chr1', 100, 'A', 'T', 'snv', null, null),
      [
        new Annotator(new AnnotatorDetails('score', 'desc', [new Resource('rid', 'rurl')]), [
          new Attribute('score', 'score desc', 'AF', null,
            {value: null, histogramLink: null} as Result),
        ]),
      ],
    );

    component.report = report;
    component.saveReport();

    const savedText = await (saveAsSpy.mock.calls[0][0] as Blob).text();
    expect(savedText).toContain('score\t\tscore desc\n');
  });

  it('should return unfold_more when attribute has no sort state', () => {
    const attribute = new Attribute('attr1', 'desc', 'AF', null, new Result(null, null));
    expect(component.getSortIcon('Value', attribute)).toBe('unfold_more');
  });

  it('should return unfold_more when queried column differs from sorted column', () => {
    const attribute = new Attribute('attr1', 'desc', 'AF', null, new Result(
      new Map([['K', 1]]), null
    ));
    component.sort('Key', attribute);
    expect(component.getSortIcon('Value', attribute)).toBe('unfold_more');
  });

  it('should return keyboard_arrow_up when column is sorted ascending', () => {
    const attribute = new Attribute('attr1', 'desc', 'AF', null, new Result(
      new Map([['K', 1]]), null
    ));
    component.sort('Key', attribute);
    expect(component.getSortIcon('Key', attribute)).toBe('keyboard_arrow_up');
  });

  it('should return keyboard_arrow_down when column is sorted descending', () => {
    const attribute = new Attribute('attr1', 'desc', 'AF', null, new Result(
      new Map([['K', 1]]), null
    ));
    component.sort('Key', attribute);
    component.sort('Key', attribute);
    expect(component.getSortIcon('Key', attribute)).toBe('keyboard_arrow_down');
  });

  it('should not mutate attribute value when sortData is called with no sort state', () => {
    const attribute = new Attribute('attr1', 'desc', 'AF', null, new Result(
      new Map([['B', 2], ['A', 1]]), null
    ));

    component.sortData(attribute);

    expect([...(attribute.result.value as Map<string, number>).keys()]).toStrictEqual(['B', 'A']);
  });

  it('should sort array attribute values ascending', () => {
    const attribute = new Attribute('attr1', 'desc', 'AF', null, new Result(['c', 'a', 'b'], null));
    component.sort('Value', attribute);
    expect(attribute.result.value).toStrictEqual(['a', 'b', 'c']);
  });

  it('should sort array attribute values descending', () => {
    const attribute = new Attribute('attr1', 'desc', 'AF', null, new Result(['c', 'a', 'b'], null));
    component.sort('Value', attribute);
    component.sort('Value', attribute);
    expect(attribute.result.value).toStrictEqual(['c', 'b', 'a']);
  });

  it('should cast value to string array via asArray', () => {
    const arr = ['alpha', 'beta'];
    expect(component.asArray(arr)).toBe(arr);
  });

  it('should convert Map to key-value entries array via asMapEntries', () => {
    const map = new Map<string, number>([['x', 1], ['y', 2]]);
    expect(component.asMapEntries(map)).toStrictEqual([['x', 1], ['y', 2]]);
  });
});
