import 'dart:convert';
import 'dart:typed_data';
import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:file_picker/file_picker.dart';
import 'package:http/http.dart' as http;
import 'package:url_launcher/url_launcher.dart';

const apiBase = String.fromEnvironment('API_BASE_URL', defaultValue: '');
const violet = Color(0xFF7450DC);
const recommendedBoards = <(String, String)>[
  ('Canonical', 'https://job-boards.greenhouse.io/canonical'),
  ('Smart Working Solutions', 'https://jobs.lever.co/smart-working-solutions'),
  ('Educative', 'https://jobs.lever.co/educative'),
  ('Xapo Bank', 'https://job-boards.greenhouse.io/xapo61'),
];
void main() => runApp(const CareerApp());

class Api {
  String? token;
  Uri uri(String path) => apiBase.isEmpty ? Uri.base.resolve('/api/$path') : Uri.parse('$apiBase/api/$path');
  Map<String, String> get headers => {'Content-Type': 'application/json', if (token != null) 'Authorization': 'Bearer $token'};
  Future<dynamic> request(String method, String path, [Object? body]) async {
    final u = uri(path);
    final r = switch (method) {
      'POST' => await http.post(u, headers: headers, body: jsonEncode(body ?? {})),
      'DELETE' => await http.delete(u, headers: headers),
      _ => await http.get(u, headers: headers),
    };
    dynamic data;
    try { data = jsonDecode(r.body); } catch (_) { data = {'detail': r.body}; }
    if (r.statusCode >= 400) throw Exception(data['detail'] ?? 'Request failed (${r.statusCode})');
    return data;
  }
  Future<void> login(String password) async { token = (await request('POST', 'auth/login', {'password': password}))['token']; }
  Future<Uint8List> getBytes(String path) async {
    final r=await http.get(uri(path),headers:{if(token!=null)'Authorization':'Bearer $token'});
    if(r.statusCode>=400)throw Exception('Download failed');
    return r.bodyBytes;
  }
  Future<void> upload(String kind, PlatformFile file) async {
    final req = http.MultipartRequest('POST', uri('documents'));
    req.headers['Authorization'] = 'Bearer $token';
    req.fields['kind'] = kind;
    req.files.add(http.MultipartFile.fromBytes('file', file.bytes!, filename: file.name));
    final response = await http.Response.fromStream(await req.send());
    if (response.statusCode >= 400) throw Exception(jsonDecode(response.body)['detail']);
  }
}
final api = Api();

class CareerApp extends StatelessWidget {
  const CareerApp({super.key});
  @override Widget build(BuildContext context) => MaterialApp(
    title: 'Career Atlas', debugShowCheckedModeBanner: false,
    theme: ThemeData(useMaterial3: true, colorScheme: ColorScheme.fromSeed(seedColor: violet),
      scaffoldBackgroundColor: const Color(0xFFF7F7FB), cardTheme: const CardThemeData(color: Colors.white, elevation: 0)),
    home: const Gate());
}
class Gate extends StatefulWidget { const Gate({super.key}); @override State<Gate> createState() => _GateState(); }
class _GateState extends State<Gate> {
  final controller = TextEditingController(); bool busy = false; String? error;
  @override void dispose(){controller.dispose();super.dispose();}
  Future<void> signIn() async { setState((){busy=true;error=null;}); try {await api.login(controller.text); if(mounted) Navigator.of(context).pushReplacement(MaterialPageRoute(builder: (_) => const Workspace()));} catch(e){setState(()=>error='$e');} finally {if(mounted)setState(()=>busy=false);} }
  @override Widget build(BuildContext context) => Scaffold(body: Center(child: ConstrainedBox(constraints: const BoxConstraints(maxWidth: 420),child: Padding(padding: const EdgeInsets.all(24),child: Column(mainAxisSize: MainAxisSize.min,crossAxisAlignment: CrossAxisAlignment.start,children:[
    const CircleAvatar(radius: 30, backgroundColor: violet,child: Text('✦',style: TextStyle(color: Colors.white,fontSize: 30))),const SizedBox(height:24),
    Text('Career Atlas',style: Theme.of(context).textTheme.headlineLarge?.copyWith(fontWeight: FontWeight.bold)),const SizedBox(height:8),
    const Text('Your private job search workspace. Sign in to see company roles and applications.'),const SizedBox(height:24),
    TextField(controller:controller, obscureText:true, onSubmitted:(_)=>signIn(),decoration:const InputDecoration(labelText:'Dashboard password',border:OutlineInputBorder())),
    if(error!=null) Padding(padding:const EdgeInsets.only(top:12),child:Text(error!,style:const TextStyle(color:Colors.red))),const SizedBox(height:15),
    FilledButton(onPressed:busy?null:signIn,child:Text(busy?'Signing in…':'Open workspace')),
  ])))));
}

class Workspace extends StatefulWidget { const Workspace({super.key}); @override State<Workspace> createState()=>_WorkspaceState(); }
class _WorkspaceState extends State<Workspace> {
  int tab=0; bool busy=false;
  List<dynamic> jobs=[],sources=[],documents=[],applications=[],jobStatuses=[],submittedApps=[],metrics=[],messages=[],followups=[],questions=[],resumeVariants=[],research=[];
  Map<String,dynamic> dashboard={};
  String query='', statusFilter='all', qualityMode='balanced';
  String applicationQuery='';
  bool reviewOnly=false;
  @override void initState(){super.initState();reload();}
  Future<void> reload() async { try {final result=await Future.wait([api.request('GET','jobs'),api.request('GET','sources'),api.request('GET','documents'),api.request('GET','applications'),api.request('GET','job-statuses'),api.request('GET','applications/submitted'),api.request('GET','v3/dashboard'),api.request('GET','v3/metrics'),api.request('GET','v3/messages'),api.request('GET','v3/followups'),api.request('GET','v3/questions'),api.request('GET','v3/resume-variants'),api.request('GET','v3/research')]);if(mounted)setState((){jobs=result[0];sources=result[1];documents=result[2];applications=result[3];jobStatuses=result[4];submittedApps=result[5];dashboard=Map<String,dynamic>.from(result[6] as Map);metrics=result[7];messages=result[8];followups=result[9];questions=result[10];resumeVariants=result[11];research=result[12];qualityMode=(dashboard['qualityMode']??'balanced').toString();});}catch(e){message('$e');} }
  void message(String text){if(mounted)ScaffoldMessenger.of(context).showSnackBar(SnackBar(content:Text(text)));}
  Future<void> scan() async {setState(()=>busy=true);try{final result=await api.request('POST','scan');await reload();final errors=(result['errors'] as List).cast<String>();message('Added ${result['added']} jobs.${errors.isEmpty?'':' Board errors: ${errors.join(', ')}'}');}catch(e){message('$e');}finally{if(mounted)setState(()=>busy=false);} }
  Future<void> open(String url) async {final uri=Uri.tryParse(url);if(uri!=null && ['https','http'].contains(uri.scheme))await launchUrl(uri,mode:LaunchMode.externalApplication);}
  Future<void> upload(String kind) async {try{final result=await FilePicker.platform.pickFiles(type:FileType.custom,allowedExtensions:['pdf'],withData:true);if(result==null)return;await api.upload(kind,result.files.single);await reload();message('$kind uploaded securely.');}catch(e){message('$e');}}
  Future<void> prepare(Map job,String kind) async {setState(()=>busy=true);try{final a=await api.request('POST','applications/prepare',{'job_id':job['id'],'document_kind':kind});await reload();if(mounted)showDialog(context:context,builder:(c)=>AlertDialog(title:Text('Application draft · ${job['company']}'),content:SingleChildScrollView(child:Text('Score ${a['score']}\n\n${a['rationale']}\n\n${a['draft']}')),actions:[TextButton(onPressed:()=>Navigator.pop(c),child:const Text('Close')),TextButton(onPressed:(){Navigator.pop(c);open(job['applyUrl']);},child:const Text('Open official form'))]));}catch(e){message('$e');}finally{if(mounted)setState(()=>busy=false);} }
  String statusFor(int jobId) => jobStatuses.where((s)=>s['jobId']==jobId).map((s)=>s['status'] as String).firstOrNull ?? 'not_submitted';
  Future<void> markStatus(Map job,String status) async {
    try { await api.request('POST','job-statuses',{'job_id':job['id'],'status':status});await reload();message(status=='submitted'?'Marked submitted: ${job['title']}':'Marked not submitted: ${job['title']}'); }
    catch(e){message('$e');}
  }
  Future<void> addSource() async {
    final company=TextEditingController(),website=TextEditingController();
    await showDialog(context:context,builder:(c)=>AlertDialog(title:const Text('Add company careers page'),content:Column(mainAxisSize:MainAxisSize.min,children:[
      TextField(controller:company,decoration:const InputDecoration(labelText:'Company name')),
      TextField(controller:website,decoration:const InputDecoration(labelText:'Greenhouse, Lever, Ashby or SmartRecruiters URL',hintText:'https://jobs.ashbyhq.com/company')),
    ]),actions:[TextButton(onPressed:()=>Navigator.pop(c),child:const Text('Cancel')),FilledButton(onPressed:()async{
      try{await api.request('POST','sources',{'company':company.text,'website':website.text.trim()});if(c.mounted)Navigator.pop(c);await reload();}
      catch(e){message('$e');}
    },child:const Text('Add board'))]));company.dispose();website.dispose();
  }
  Future<void> addRecommended(String company, String website) async {
    try {
      await api.request('POST', 'sources', {'company': company, 'website': website});
      await reload();
      message('$company added. Tap Find jobs to scan its open roles.');
    } catch (e) { message('$e'); }
  }
  Future<void> setQuality(String mode) async {
    try { final r=await api.request('POST','v3/settings/quality',{'mode':mode}); setState(()=>qualityMode=mode); await reload(); message('Quality mode: $mode · threshold ${r['threshold']}'); }
    catch(e){message('$e');}
  }
  Future<void> showEvidence(int applicationId) async {
    try {
      final items=(await api.request('GET','v3/artifacts/$applicationId') as List);
      if(items.isEmpty){message('No submission screenshots stored for this application.');return;}
      final widgets=<Widget>[];
      for(final item in items){
        final bytes=await api.getBytes('v3/artifacts/file/${item['id']}');
        widgets.add(Padding(padding:const EdgeInsets.only(bottom:14),child:Column(crossAxisAlignment:CrossAxisAlignment.start,children:[
          Text('${item['kind']} · ${item['createdAt']}',style:const TextStyle(fontWeight:FontWeight.w600)),
          const SizedBox(height:6),ClipRRect(borderRadius:BorderRadius.circular(10),child:Image.memory(bytes,width:720,fit:BoxFit.fitWidth))
        ])));
      }
      if(mounted)showDialog(context:context,builder:(c)=>AlertDialog(title:const Text('Submission evidence'),content:SizedBox(width:760,child:SingleChildScrollView(child:Column(children:widgets))),actions:[TextButton(onPressed:()=>Navigator.pop(c),child:const Text('Close'))]));
    }catch(e){message('$e');}
  }
  Future<void> mapQuestion(Map question) async {
    final current=(question['answerKey']??'').toString();
    String selected=current;
    const options=['','name','email','phone','location','linkedin','github','portfolio','availability','salary','work_authorized','requires_sponsorship'];
    await showDialog(context:context,builder:(c)=>StatefulBuilder(builder:(c,setLocal)=>AlertDialog(
      title:const Text('Learn application field'),
      content:Column(mainAxisSize:MainAxisSize.min,crossAxisAlignment:CrossAxisAlignment.start,children:[
        Text(question['label']??'',maxLines:4,overflow:TextOverflow.ellipsis),
        const SizedBox(height:10),
        if(question['sensitive']==true)const Text('Sensitive field: normal profile memory is intentionally disabled.'),
        if(question['sensitive']!=true)DropdownButtonFormField<String>(
          value:options.contains(selected)?selected:'',
          decoration:const InputDecoration(labelText:'Reusable profile value'),
          items:options.map((x)=>DropdownMenuItem(value:x,child:Text(x.isEmpty?'Do not auto-fill':x))).toList(),
          onChanged:(x)=>setLocal(()=>selected=x??''),
        ),
      ]),
      actions:[
        TextButton(onPressed:()=>Navigator.pop(c),child:const Text('Cancel')),
        FilledButton(onPressed:question['sensitive']==true?null:()async{
          try{
            await api.request('POST','v3/questions/${question['id']}',{'answer_key':selected,'selector_hint':question['selectorHint']??''});
            if(c.mounted)Navigator.pop(c);
            await reload();
          }catch(e){message('$e');}
        },child:const Text('Save mapping'))
      ],
    )));
  }
  Future<void> showInterviewPrep(int applicationId) async {
    try{
      final r=await api.request('GET','v3/interview-prep/$applicationId');
      final content=(r['content']??'').toString();
      if(content.isEmpty){message('No interview-prep package has been generated for this application yet.');return;}
      if(mounted)showDialog(context:context,builder:(c)=>AlertDialog(
        title:const Text('Interview preparation'),
        content:SizedBox(width:720,child:SingleChildScrollView(child:SelectableText(content))),
        actions:[TextButton(onPressed:()=>Navigator.pop(c),child:const Text('Close'))],
      ));
    }catch(e){message('$e');}
  }

  Future<void> locateSubmittedApplication() async {
    final controller=TextEditingController();
    await showDialog(context:context,builder:(c)=>AlertDialog(
      title:const Text('Find a submitted application'),
      content:Column(mainAxisSize:MainAxisSize.min,crossAxisAlignment:CrossAxisAlignment.start,children:[
        const Text('Paste the employer application or confirmation URL. Career Atlas will find the matching job and application.'),
        const SizedBox(height:10),
        TextField(
          controller:controller,
          keyboardType:TextInputType.url,
          autocorrect:false,
          decoration:const InputDecoration(
            labelText:'Confirmation URL',
            hintText:'https://job-boards.greenhouse.io/company/jobs/123/confirmation',
            border:OutlineInputBorder(),
          ),
        ),
      ]),
      actions:[
        TextButton(onPressed:()=>Navigator.pop(c),child:const Text('Cancel')),
        FilledButton.icon(onPressed:()async{
          final value=controller.text.trim();
          if(value.isEmpty)return;
          try{
            final result=await api.request('POST','applications/locate',{'url':value});
            final job=Map<String,dynamic>.from(result['job'] as Map);
            final app=result['application'];
            if(c.mounted)Navigator.pop(c);
            setState((){
              tab=1;
              reviewOnly=false;
              applicationQuery=(job['title']??job['company']??job['id'].toString()).toString();
            });
            if(app==null){
              message('Job found: ${job['title']}. No Career Atlas application record exists yet.');
            }else{
              message('Found ${job['company']} · ${job['title']}.');
            }
          }catch(e){message('$e');}
        },icon:const Icon(Icons.search),label:const Text('Find application'))
      ],
    ));
    controller.dispose();
  }

  Future<void> reviewWithAgent(Map application) async {
    try{
      final payload=Map<String,dynamic>.from(await api.request('GET','v3/applications/${application['id']}/review-fields') as Map);
      final fields=(payload['fields'] as List? ?? []).map((x)=>Map<String,dynamic>.from(x as Map)).toList();
      if(fields.isEmpty){
        message('No answer is needed from you. This is an agent/form-adapter issue, so do not refill the whole form manually.');
        return;
      }

      final controllers=<int,TextEditingController>{};
      for(final field in fields){
        controllers[field['id'] as int]=TextEditingController(text:(field['answer']??'').toString());
      }

      if(!mounted)return;
      await showDialog(context:context,builder:(dialogContext)=>StatefulBuilder(builder:(dialogContext,setLocal){
        bool sending=false;
        return AlertDialog(
          title:Text('Answer only the missing fields · ${payload['company']??''}'),
          content:SizedBox(
            width:640,
            child:SingleChildScrollView(child:Column(crossAxisAlignment:CrossAxisAlignment.start,children:[
              const Text(
                'Career Atlas will refill your name, email, phone, links, education, tailored résumé and every other known field automatically. You only answer the unresolved items below.'
              ),
              const SizedBox(height:12),
              ...fields.map((field)=>Padding(
                padding:const EdgeInsets.only(bottom:14),
                child:Column(crossAxisAlignment:CrossAxisAlignment.start,children:[
                  Text(field['label']??'',style:const TextStyle(fontWeight:FontWeight.w600)),
                  if(field['sensitive']==true)Padding(
                    padding:const EdgeInsets.only(top:4,bottom:6),
                    child:Text(
                      'Protected/personal question — only you can provide this answer. Career Atlas will use it only for this application, will not infer it, and will not reuse it for other applications.',
                      style:TextStyle(color:Colors.orange.shade800,fontSize:12),
                    ),
                  ),
                  const SizedBox(height:5),
                  TextField(
                    controller:controllers[field['id'] as int],
                    minLines:1,
                    maxLines:3,
                    decoration:InputDecoration(
                      hintText:field['sensitive']==true?'Enter your answer exactly as you want it submitted':'Your answer',
                      border:const OutlineInputBorder(),
                    ),
                  ),
                ]),
              )),
            ])),
          ),
          actions:[
            TextButton(onPressed:sending?null:()=>Navigator.pop(dialogContext),child:const Text('Cancel')),
            FilledButton.icon(
              onPressed:sending?null:()async{
                final answers=fields.map((field)=>({
                  'field_id':field['id'],
                  'answer':controllers[field['id'] as int]!.text.trim(),
                })).toList();
                if(answers.any((x)=>(x['answer'] as String).isEmpty)){
                  message('Please answer each missing field shown here.');
                  return;
                }
                setLocal(()=>sending=true);
                try{
                  final result=await api.request(
                    'POST',
                    'v3/applications/${application['id']}/review-submit',
                    {'answers':answers},
                  );
                  final status=(result['status']??'').toString();
                  if(status=='retry_started'){
                    if(dialogContext.mounted)Navigator.pop(dialogContext);
                    message('Saved. LangGraph is refilling the complete form and retrying now. You do not need to reopen the employer form.');
                    await reload();
                    Future.delayed(const Duration(seconds:20),(){if(mounted)reload();});
                  }else{
                    final missing=(result['missing'] as List? ?? []);
                    message(missing.isEmpty
                      ? (result['message']??'Answers saved for the automatic retry.').toString()
                      : 'Still missing: ${missing.join(' · ')}');
                  }
                }catch(e){
                  message('$e');
                }finally{
                  if(dialogContext.mounted)setLocal(()=>sending=false);
                }
              },
              icon:sending
                ? const SizedBox(width:16,height:16,child:CircularProgressIndicator(strokeWidth:2))
                : const Icon(Icons.auto_awesome),
              label:Text(sending?'Starting agent…':'Save answers & let agent finish'),
            ),
          ],
        );
      }));
      for(final controller in controllers.values){controller.dispose();}
    }catch(e){message('$e');}
  }

  Future<void> confirmApplication(Map application) async {
    final receipt=TextEditingController();
    await showDialog(context:context,builder:(c)=>AlertDialog(
      title:const Text('I submitted this application'),
      content:Column(mainAxisSize:MainAxisSize.min,crossAxisAlignment:CrossAxisAlignment.start,children:[
        const Text('Use this only after you have actually submitted the employer form. Career Atlas will move it to Submitted and update the dashboard immediately.'),
        const SizedBox(height:10),
        TextField(controller:receipt,decoration:const InputDecoration(
          labelText:'Confirmation (optional)',
          hintText:'e.g. Thank you for applying, confirmation number, or email subject'
        )),
      ]),
      actions:[
        TextButton(onPressed:()=>Navigator.pop(c),child:const Text('Cancel')),
        FilledButton.icon(onPressed:()async{
          try{
            await api.request('POST','applications/${application['id']}/confirm',{'receipt':receipt.text.trim()});
            if(c.mounted)Navigator.pop(c);
            await reload();
            if(mounted)setState(()=>tab=2);
            message('Application moved to Submitted.');
          }catch(e){message('$e');}
        },icon:const Icon(Icons.check_circle_outline),label:const Text('I submitted it'))
      ],
    ));receipt.dispose();
  }
  @override Widget build(BuildContext context){
    final wide=MediaQuery.sizeOf(context).width>760;
    final filtered=jobs.where((x){
      final matchesText='${x['title']} ${x['company']} ${x['location']}'.toLowerCase().contains(query.trim().toLowerCase());
      final matchesStatus=statusFilter=='all'||statusFor(x['id'] as int)==statusFilter;
      return matchesText&&matchesStatus;
    }).toList();
    const destinations=[
      NavigationDestination(icon:Icon(Icons.work_outline),label:'Jobs'),
      NavigationDestination(icon:Icon(Icons.approval_outlined),label:'Applications'),
      NavigationDestination(icon:Icon(Icons.check_circle_outline),label:'Submitted'),
      NavigationDestination(icon:Icon(Icons.dashboard_outlined),label:'Dashboard'),
      NavigationDestination(icon:Icon(Icons.settings_outlined),label:'Setup'),
    ];
    const rail=[
      NavigationRailDestination(icon:Icon(Icons.work_outline),label:Text('Jobs')),
      NavigationRailDestination(icon:Icon(Icons.approval_outlined),label:Text('Applications')),
      NavigationRailDestination(icon:Icon(Icons.check_circle_outline),label:Text('Submitted')),
      NavigationRailDestination(icon:Icon(Icons.dashboard_outlined),label:Text('Dashboard')),
      NavigationRailDestination(icon:Icon(Icons.settings_outlined),label:Text('Setup')),
    ];
    return Scaffold(
      appBar:AppBar(title:const Text('✦ Career Atlas',style:TextStyle(fontWeight:FontWeight.bold)),actions:[
        IconButton(tooltip:'Refresh',onPressed:reload,icon:const Icon(Icons.refresh)),
        IconButton(tooltip:'Sign out',onPressed:(){api.token=null;Navigator.of(context).pushReplacement(MaterialPageRoute(builder:(_)=>const Gate()));},icon:const Icon(Icons.logout))
      ]),
      bottomNavigationBar:wide?null:NavigationBar(selectedIndex:tab,onDestinationSelected:(x)=>setState(()=>tab=x),destinations:destinations),
      body:Row(children:[
        if(wide)NavigationRail(selectedIndex:tab,onDestinationSelected:(x)=>setState(()=>tab=x),labelType:NavigationRailLabelType.all,destinations:rail),
        Expanded(child:Center(child:ConstrainedBox(constraints:const BoxConstraints(maxWidth:1180),child:Padding(
          padding:const EdgeInsets.all(16),
          child:switch(tab){0=>jobsView(filtered),1=>applicationsView(),2=>submittedView(),3=>dashboardView(),_=>setupView()}
        ))))
      ])
    );
  }
  Widget jobsView(List<dynamic> filtered)=>Column(crossAxisAlignment:CrossAxisAlignment.start,children:[
    Row(children:[Expanded(child:Text('${jobs.length} company jobs',style:Theme.of(context).textTheme.headlineSmall)),FilledButton.icon(onPressed:busy?null:scan,icon:const Icon(Icons.auto_awesome),label:Text(busy?'Scanning…':'Find jobs'))]),
    const SizedBox(height:12),TextField(onChanged:(x)=>setState(()=>query=x),decoration:const InputDecoration(prefixIcon:Icon(Icons.search),hintText:'Search roles, companies or locations',filled:true,border:OutlineInputBorder())),
    const SizedBox(height:8),Wrap(spacing:8,children:[
      for(final option in [('all','All'),('not_submitted','To apply'),('submitted','Submitted')])
        ChoiceChip(label:Text(option.$2),selected:statusFilter==option.$1,onSelected:(_)=>setState(()=>statusFilter=option.$1)),
    ]),Text('Showing ${filtered.length} of ${jobs.length} roles'),
    const SizedBox(height:10),const Text('Explore other job sites (opens their live website)'),const SizedBox(height:5),
    Wrap(spacing:8,runSpacing:4,children:[
      OutlinedButton(onPressed:()=>open('https://pk.indeed.com/jobs?q=${Uri.encodeQueryComponent(query.trim().isEmpty ? 'software engineer' : query.trim())}&l=Pakistan'),child:const Text('Indeed ↗')),
      OutlinedButton(onPressed:()=>open('https://www.glassdoor.com/Job/pakistan-jobs-SRCH_IL.0,8_IN192.htm'),child:const Text('Glassdoor ↗')),
      OutlinedButton(onPressed:()=>open('https://www.linkedin.com/jobs/search/?keywords=${Uri.encodeQueryComponent(query.trim().isEmpty ? 'software engineer' : query.trim())}&f_WT=2'),child:const Text('LinkedIn ↗')),
      OutlinedButton(onPressed:()=>open('https://wellfound.com/jobs'),child:const Text('Wellfound ↗')),
      OutlinedButton(onPressed:()=>open('https://remoteok.com/'),child:const Text('Remote OK ↗')),
      OutlinedButton(onPressed:()=>open('https://remotive.com/remote-jobs'),child:const Text('Remotive ↗')),
      OutlinedButton(onPressed:()=>open('https://jobicy.com/'),child:const Text('Jobicy ↗')),
      OutlinedButton(onPressed:()=>open('https://himalayas.app/jobs'),child:const Text('Himalayas ↗')),
      OutlinedButton(onPressed:()=>open('https://weworkremotely.com/'),child:const Text('We Work Remotely ↗')),
      OutlinedButton(onPressed:()=>open('https://www.workingnomads.com/remote-jobs'),child:const Text('Working Nomads ↗')),
      OutlinedButton(onPressed:()=>open('https://nodesk.co/remote-jobs/'),child:const Text('NoDesk ↗')),
      OutlinedButton(onPressed:()=>open('https://remote.co/remote-jobs/'),child:const Text('Remote.co ↗')),
      OutlinedButton(onPressed:()=>open('https://www.arbeitnow.com/'),child:const Text('Arbeitnow ↗')),
      OutlinedButton(onPressed:()=>open('https://www.rozee.pk/EN/search/software-engineer-jobs-in-pakistan'),child:const Text('ROZEE.PK ↗')),
      OutlinedButton(onPressed:()=>open('https://www.mustakbil.com/'),child:const Text('Mustakbil ↗')),
    ]),const Text('Remote OK, Remotive, Jobicy, Himalayas, We Work Remotely and Arbeitnow are also imported automatically. Other sites open as live search/apply sources.'),const SizedBox(height:10),
    Expanded(child:filtered.isEmpty?Center(child:Text(jobs.isEmpty?'Add a company board in Setup, then tap Find jobs.':'No roles match these filters.')):ListView.builder(itemCount:filtered.length,itemBuilder:(c,i){
      final j=filtered[i] as Map;final submitted=statusFor(j['id'] as int)=='submitted';
      return Card(child:Padding(padding:const EdgeInsets.all(15),child:Column(crossAxisAlignment:CrossAxisAlignment.start,children:[
        Text(j['title']??'',style:Theme.of(context).textTheme.titleMedium?.copyWith(fontWeight:FontWeight.bold)),
        Text('${j['company']} · ${j['location']} · ${j['provider']}'),
        Text(submitted?'Submitted':'Not submitted',style:TextStyle(color:submitted?Colors.green.shade700:null,fontWeight:FontWeight.w600)),
        const SizedBox(height:10),Wrap(spacing:8,runSpacing:8,children:[
          OutlinedButton(onPressed:()=>open(j['applyUrl']),child:const Text('Official form ↗')),
          OutlinedButton(onPressed:busy?null:documents.isEmpty?()=>upload('Resume'):()=>prepare(j,documents.any((d)=>d['kind']=='Resume')?'Resume':'CV'),child:Text(documents.isEmpty?'Upload resume for AI match':'Match & draft')),
          OutlinedButton(onPressed:()=>markStatus(j,submitted?'not_submitted':'submitted'),child:Text(submitted?'Undo submitted':'I submitted this')),
        ])
      ])));
    }))
  ]);
  Widget applicationsView(){
    final reviewApps=applications.where((x)=>(x as Map)['status']=='needs_human').toList();
    final base=reviewOnly?reviewApps:applications;
    final needle=applicationQuery.trim().toLowerCase();
    final visible=base.where((item){
      if(needle.isEmpty)return true;
      final a=item as Map;
      final related=jobs.where((j)=>j['id']==a['jobId']).toList();
      final job=related.isEmpty?null:related.first as Map?;
      final hay=[
        a['id'],a['jobId'],a['score'],a['status'],a['receipt'],a['rationale'],
        job?['company'],job?['title'],job?['provider'],job?['location'],job?['applyUrl'],job?['url']
      ].map((x)=>(x??'').toString().toLowerCase()).join(' ');
      return hay.contains(needle);
    }).toList();
    return Column(crossAxisAlignment:CrossAxisAlignment.start,children:[
      Row(children:[
        Expanded(child:Text('Applications',style:Theme.of(context).textTheme.headlineSmall)),
        if(reviewOnly)Chip(label:Text('${reviewApps.length} need review'))
      ]),
      const SizedBox(height:8),
      Text('${jobStatuses.where((s)=>s['status']=='submitted').length} confirmed submitted · ${reviewApps.length} need review · ${applications.length} application records'),
      const SizedBox(height:10),
      TextField(
        onChanged:(x)=>setState(()=>applicationQuery=x),
        controller:TextEditingController(text:applicationQuery)..selection=TextSelection.collapsed(offset:applicationQuery.length),
        decoration:InputDecoration(
          prefixIcon:const Icon(Icons.search),
          hintText:'Search company, role, provider, score, job ID or URL',
          suffixIcon:applicationQuery.isEmpty?null:IconButton(
            icon:const Icon(Icons.clear),
            onPressed:()=>setState(()=>applicationQuery=''),
          ),
          filled:true,
          border:const OutlineInputBorder(),
        ),
      ),
      const SizedBox(height:8),
      Wrap(spacing:8,runSpacing:8,children:[
        ChoiceChip(label:Text('All (${applications.length})'),selected:!reviewOnly,onSelected:(_)=>setState(()=>reviewOnly=false)),
        ChoiceChip(label:Text('Needs review (${reviewApps.length})'),selected:reviewOnly,onSelected:(_)=>setState(()=>reviewOnly=true)),
        OutlinedButton.icon(onPressed:locateSubmittedApplication,icon:const Icon(Icons.link),label:const Text('Paste confirmation URL')),
      ]),
      if(needle.isNotEmpty)Padding(
        padding:const EdgeInsets.only(top:7),
        child:Text('Showing ${visible.length} matching application${visible.length==1?'':'s'}'),
      ),
      const SizedBox(height:10),
      Expanded(child:visible.isEmpty
        ? Center(child:Text(reviewOnly?'No applications currently need review.':'No application records yet.'))
        : ListView(children:[
          ...visible.map((item){
            final a=item as Map;
            final related=jobs.where((j)=>j['id']==a['jobId']).toList();
            final embedded=(a['title']!=null || a['company']!=null || a['applyUrl']!=null)?a:null;
            final job=related.isEmpty?(embedded as Map?):related.first as Map?;
            final needs=a['status']=='needs_human';
            final queued=a['status']=='ready_for_retry' || a['reviewQueued']==true;
            final reviewQuestions=(a['reviewQuestions'] as List? ?? []);
            final unanswered=reviewQuestions.where((q)=>(q as Map)['answered']!=true).length;
            final receipt=(a['receipt']??'').toString();
            final submitted=statusFor(a['jobId'] as int)=='submitted';
            return Card(child:Padding(padding:const EdgeInsets.all(12),child:Column(crossAxisAlignment:CrossAxisAlignment.start,children:[
              Row(crossAxisAlignment:CrossAxisAlignment.start,children:[
                Expanded(child:Column(crossAxisAlignment:CrossAxisAlignment.start,children:[
                  Text(job?['title']??'Job #${a['jobId']}',style:Theme.of(context).textTheme.titleMedium?.copyWith(fontWeight:FontWeight.bold)),
                  Text('${job?['company']??''} · ${job?['provider']??''}'),
                ])),
                Chip(label:Text(queued?'Agent retry queued':(needs?'Needs review':(submitted?'Submitted':(a['status']??'Prepared').toString().replaceAll('_',' ')))))
              ]),
              Text('Match score: ${a['score']}'),
              if(needs)Container(
                width:double.infinity,
                margin:const EdgeInsets.only(top:8,bottom:8),
                padding:const EdgeInsets.all(10),
                decoration:BoxDecoration(color:Colors.amber.withValues(alpha:.12),borderRadius:BorderRadius.circular(10)),
                child:Column(crossAxisAlignment:CrossAxisAlignment.start,children:[
                  Text(reviewQuestions.isNotEmpty?'Your input needed: $unanswered field${unanswered==1?'':'s'}':'Technical form blocker',style:const TextStyle(fontWeight:FontWeight.bold)),
                  const SizedBox(height:4),
                  if(reviewQuestions.isNotEmpty)
                    ...reviewQuestions.where((q)=>(q as Map)['answered']!=true).take(5).map((q)=>Padding(
                      padding:const EdgeInsets.only(bottom:3),
                      child:Text('• ${q['label']}${q['sensitive']==true?'  (answer yourself)':''}'),
                    ))
                  else
                    Text(receipt,maxLines:3,overflow:TextOverflow.ellipsis),
                ]),
              ),
              if((a['rationale']??'').toString().isNotEmpty)Text(a['rationale']??''),
              if((a['draft']??'').toString().isNotEmpty)ExpansionTile(title:const Text('Read application draft'),children:[Padding(padding:const EdgeInsets.all(12),child:SelectableText(a['draft']??''))]),
              Wrap(spacing:8,runSpacing:8,children:[
                OutlinedButton(onPressed:job==null?null:()=>open(job['applyUrl']),child:const Text('Official form ↗')),
                if(needs && reviewQuestions.isNotEmpty)FilledButton.icon(onPressed:()=>reviewWithAgent(a),icon:const Icon(Icons.auto_awesome),label:Text('Answer $unanswered field${unanswered==1?'':'s'} & retry')),
                if(queued)const Chip(avatar:Icon(Icons.autorenew,size:16),label:Text('LangGraph retrying')),
                if(needs)OutlinedButton(onPressed:job==null?null:()=>open(job['applyUrl']),child:const Text('Open form manually ↗')),
                if(!submitted)FilledButton.icon(onPressed:()=>confirmApplication(a),icon:const Icon(Icons.check_circle_outline),label:const Text('I submitted this')),
              ]),
            ])));
          }),
        ]))
    ]);
  }
  Widget submittedView()=>Column(crossAxisAlignment:CrossAxisAlignment.start,children:[
    Row(children:[
      Expanded(child:Text('Submitted Applications',style:Theme.of(context).textTheme.headlineSmall)),
      Chip(label:Text('${submittedApps.length} confirmed'))
    ]),
    const SizedBox(height:6),
    const Text('Only applications with a confirmed submission status and receipt appear here.'),
    const SizedBox(height:12),
    Expanded(child:submittedApps.isEmpty
      ? const Center(child:Text('No confirmed submissions yet.'))
      : ListView.builder(itemCount:submittedApps.length,itemBuilder:(c,i){
          final a=submittedApps[i] as Map;
          return Card(child:Padding(padding:const EdgeInsets.all(14),child:Column(crossAxisAlignment:CrossAxisAlignment.start,children:[
            Row(crossAxisAlignment:CrossAxisAlignment.start,children:[
              const Icon(Icons.check_circle,color:Colors.green),
              const SizedBox(width:8),
              Expanded(child:Column(crossAxisAlignment:CrossAxisAlignment.start,children:[
                Text(a['title']??'',style:Theme.of(context).textTheme.titleMedium?.copyWith(fontWeight:FontWeight.bold)),
                Text('${a['company']} · ${a['location']} · ${a['provider']}'),
              ]))
            ]),
            const SizedBox(height:8),
            Text('Match score: ${a['score']}'),
            Text('Submitted: ${a['submittedAt']}'),
            if((a['receipt']??'').toString().isNotEmpty) Text('Receipt: ${a['receipt']}'),
            const SizedBox(height:8),
            Wrap(spacing:8,children:[OutlinedButton(onPressed:()=>open(a['applyUrl']),child:const Text('View job ↗')),OutlinedButton(onPressed:()=>showEvidence(a['applicationId'] as int),child:const Text('Evidence'))]),
          ])));
        }))
  ]);
  Widget _statCard(String label,Object? value,IconData icon,{VoidCallback? onTap})=>SizedBox(width:170,child:Card(child:InkWell(
    borderRadius:BorderRadius.circular(12),onTap:onTap,
    child:Padding(padding:const EdgeInsets.all(14),child:Column(crossAxisAlignment:CrossAxisAlignment.start,children:[
      Icon(icon,color:violet),const SizedBox(height:8),Text('${value??0}',style:Theme.of(context).textTheme.headlineMedium?.copyWith(fontWeight:FontWeight.bold)),
      Row(children:[Expanded(child:Text(label)),if(onTap!=null)const Icon(Icons.chevron_right,size:18)])
    ]))
  )));
  Widget dashboardView()=>ListView(children:[
    Row(children:[Expanded(child:Text('Automation Dashboard',style:Theme.of(context).textTheme.headlineSmall)),Chip(label:Text('Threshold ${dashboard['threshold']??75}+'))]),
    const SizedBox(height:8),const Text('Live Career Atlas v3 pipeline: discovery, scoring, form automation, submission evidence and recruiter follow-up.'),
    const SizedBox(height:12),
    Wrap(spacing:10,runSpacing:10,children:[
      _statCard('Jobs',dashboard['jobs'],Icons.work_outline),
      _statCard('Applications',dashboard['applications'],Icons.description_outlined),
      _statCard('Confirmed',dashboard['submitted'],Icons.check_circle_outline),
      _statCard('Needs review',dashboard['needsAttention'],Icons.warning_amber_outlined,onTap:()=>setState((){reviewOnly=true;tab=1;})),
      _statCard('Avg probability','${dashboard['averageProbability']??0}%',Icons.insights_outlined),
    ]),
    const SizedBox(height:18),Text('Quality mode',style:Theme.of(context).textTheme.titleMedium),const SizedBox(height:7),
    Wrap(spacing:8,children:[
      for(final mode in [('conservative','Conservative · 85+'),('balanced','Balanced · 75+'),('aggressive','Aggressive · 65+')])
        ChoiceChip(label:Text(mode.$2),selected:qualityMode==mode.$1,onSelected:(_)=>setQuality(mode.$1))
    ]),
    const SizedBox(height:18),Text('Pipeline',style:Theme.of(context).textTheme.titleMedium),const SizedBox(height:7),
    Wrap(spacing:8,runSpacing:8,children:[
      for(final stage in ['discovered','matched','prepared','applying','submitted','employer_viewed','interview','rejected','offer'])
        Chip(label:Text('${stage.replaceAll('_',' ')} · ${(dashboard['stageCounts'] as Map?)?[stage]??0}'))
    ]),
    const SizedBox(height:18),Text('Top opportunities',style:Theme.of(context).textTheme.titleMedium),
    ...metrics.take(10).map((m)=>Card(child:ListTile(
      title:Text('${m['title']} · ${m['company']}'),
      subtitle:Text('Technical ${m['technical']} · Experience ${m['experience']} · Location ${m['location']} · Difficulty ${m['difficulty']}'),
      trailing:Chip(label:Text('${m['probability']}%'))
    ))),
    const SizedBox(height:18),Text('Recruiter messages',style:Theme.of(context).textTheme.titleMedium),
    if(messages.isEmpty)const Text('No recruiter replies detected yet.'),
    ...messages.take(8).map((m)=>Card(child:ListTile(
      leading:Icon(m['actionRequired']==true?Icons.mark_email_unread_outlined:Icons.email_outlined),
      title:Text(m['subject']??''),
      subtitle:Text('${m['classification']} · ${m['sender']}',maxLines:2,overflow:TextOverflow.ellipsis),
      trailing:(m['classification']=='interview' && m['applicationId']!=null)
        ? TextButton(onPressed:()=>showInterviewPrep(m['applicationId'] as int),child:const Text('Prep'))
        : (m['actionRequired']==true?const Chip(label:Text('Action')):null)
    ))),
    const SizedBox(height:18),Text('Follow-up drafts',style:Theme.of(context).textTheme.titleMedium),
    if(followups.isEmpty)const Text('Follow-ups are prepared after 5 days with no recruiter response.'),
    ...followups.take(8).map((d)=>Card(child:ExpansionTile(
      title:Text('${d['title']} · ${d['company']}'),subtitle:Text('Status: ${d['status']} · due ${d['dueAt']??'-'}'),
      children:[Padding(padding:const EdgeInsets.all(12),child:SelectableText(d['message']??''))]
    ))),
    const SizedBox(height:18),Text('CrewAI / research results',style:Theme.of(context).textTheme.titleMedium),
    if(research.isEmpty)const Text('Research results appear after roles enter the automation pipeline.'),
    ...research.take(8).map((r)=>Card(child:ExpansionTile(
      title:Text('${r['title']} · ${r['company']}'),
      subtitle:Text('Quality ${r['qualityScore']} · ${r['source']}'),
      children:[Padding(padding:const EdgeInsets.all(12),child:Text('${r['companySummary']}\n\n${r['eligibilityNotes']}'))]
    ))),
    const SizedBox(height:18),Text('Tailored resume variants',style:Theme.of(context).textTheme.titleMedium),
    if(resumeVariants.isEmpty)const Text('Verified-content resume variants are created only for roles that clear the selected quality threshold.'),
    ...resumeVariants.take(8).map((v)=>Card(child:ListTile(
      leading:const Icon(Icons.picture_as_pdf_outlined),
      title:Text('${v['title']} · ${v['company']}'),
      subtitle:Text(v['strategy']??'',maxLines:2,overflow:TextOverflow.ellipsis),
      trailing:Text(v['filename']??'')
    ))),
    const SizedBox(height:18),Text('Learned application fields',style:Theme.of(context).textTheme.titleMedium),
    if(questions.isEmpty)const Text('New reusable non-sensitive fields will appear here after the browser encounters them.'),
    ...questions.take(12).map((q)=>Card(child:ListTile(
      leading:Icon(q['sensitive']==true?Icons.lock_outline:Icons.school_outlined),
      title:Text(q['label']??'',maxLines:2,overflow:TextOverflow.ellipsis),
      subtitle:Text('${q['host']} · ${q['sensitive']==true?'sensitive / isolated':((q['answerKey']??'').toString().isEmpty?'unmapped':'maps to ${q['answerKey']}')}'),
      trailing:q['sensitive']==true?null:TextButton(onPressed:()=>mapQuestion(q as Map),child:const Text('Map'))
    ))),
    const SizedBox(height:18),Text('Recent LangGraph events',style:Theme.of(context).textTheme.titleMedium),
    ...((dashboard['recentEvents'] as List?)??[]).take(20).map((e)=>Card(child:ListTile(
      leading:const Icon(Icons.route_outlined),title:Text('${e['stage']} · ${e['type']}'),subtitle:Text(e['message']??'',maxLines:2,overflow:TextOverflow.ellipsis),
      trailing:Text((e['createdAt']??'').toString().replaceFirst('T',' ').split('.').first)
    ))),
  ]);
  Widget setupView()=>ListView(children:[Text('Your setup',style:Theme.of(context).textTheme.headlineSmall),const SizedBox(height:14),const Text('Documents (private PDFs; selectable text required)'),...['CV','Resume'].map((kind)=>Card(child:ListTile(title:Text(kind),subtitle:Text(documents.where((d)=>d['kind']==kind).map((d)=>d['filename']).firstOrNull??'Not uploaded'),trailing:OutlinedButton(onPressed:()=>upload(kind),child:const Text('Upload'))))),const SizedBox(height:22),Row(children:[const Expanded(child:Text('Company career boards')),FilledButton(onPressed:addSource,child:const Text('Add company'))]),...sources.map((source)=>Card(child:ListTile(title:Text(source['company']),subtitle:Text('${source['provider']} · ${source['slug']}'),trailing:IconButton(icon:const Icon(Icons.delete_outline),onPressed:()async{await api.request('DELETE','sources/${source['id']}');await reload();})))),const SizedBox(height:18),Text('Suggested company boards',style:Theme.of(context).textTheme.titleMedium),const SizedBox(height:6),const Text('Add the boards you want to follow. Individual job locations and eligibility vary.'),...recommendedBoards.map((board)=>Card(child:ListTile(title:Text(board.$1),subtitle:Text(board.$2,overflow:TextOverflow.ellipsis),trailing:OutlinedButton(onPressed:sources.any((s)=>s['slug']==Uri.parse(board.$2).pathSegments.first)?null:()=>addRecommended(board.$1,board.$2),child:Text(sources.any((s)=>s['slug']==Uri.parse(board.$2).pathSegments.first)?'Added':'Add'))))),const SizedBox(height:20),const Text('Paste a Greenhouse, Lever, Ashby or SmartRecruiters careers URL to add another official company board. Career Atlas can auto-submit compatible forms, while CAPTCHAs, assessments and uncertain sensitive/legal questions remain review-only.')]);
}
