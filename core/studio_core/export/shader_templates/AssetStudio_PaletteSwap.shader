// AI Asset Studio — palette swap: each pixel's luma picks a colour from row _Row of _Ramp (256 px wide, one row per palette).
Shader "AssetStudio/Sprite/PaletteSwap"
{
    Properties
    {
        [PerRendererData] _MainTex ("Sprite Texture", 2D) = "white" {}
        _Ramp ("Ramp", 2D) = "white" {}
        _Row ("Row", Float) = 0
        _Rows ("Rows", Float) = 1
        _Strength ("Strength", Range(0, 1)) = 1
    }

    SubShader
    {
        Tags { "Queue" = "Transparent" "RenderType" = "Transparent" "RenderPipeline" = "UniversalPipeline" "IgnoreProjector" = "True" "PreviewType" = "Plane" "CanUseSpriteAtlas" = "True" }

        Blend SrcAlpha OneMinusSrcAlpha, One OneMinusSrcAlpha
        Cull Off
        ZWrite Off

        Pass
        {
            HLSLPROGRAM
            #pragma vertex vert
            #pragma fragment frag
            #pragma multi_compile_instancing
            #pragma multi_compile _ SKINNED_SPRITE

            #include "Packages/com.unity.render-pipelines.universal/Shaders/2D/Include/Core2D.hlsl"

            struct Attributes
            {
                float3 positionOS : POSITION;
                float2 uv : TEXCOORD0;
                half4 color : COLOR;
                UNITY_VERTEX_INPUT_INSTANCE_ID
                UNITY_SKINNED_VERTEX_INPUTS
            };

            struct Varyings
            {
                float4 positionCS : SV_POSITION;
                float2 uv : TEXCOORD0;
                half4 color : COLOR;
                UNITY_VERTEX_OUTPUT_STEREO
            };

            TEXTURE2D(_MainTex);
            SAMPLER(sampler_MainTex);
            TEXTURE2D(_Ramp);
            SAMPLER(sampler_Ramp);

            CBUFFER_START(UnityPerMaterial)
                float4 _MainTex_TexelSize;
                float4 _Ramp_ST;
                float _Row;
                float _Rows;
                half _Strength;
            CBUFFER_END

            Varyings vert(Attributes v)
            {
                Varyings o = (Varyings)0;
                UNITY_SETUP_INSTANCE_ID(v);
                UNITY_INITIALIZE_VERTEX_OUTPUT_STEREO(o);
                UNITY_SKINNED_VERTEX_COMPUTE(v);
                SetUpSpriteInstanceProperties();
                v.positionOS = UnityFlipSprite(v.positionOS, unity_SpriteProps.xy);
                o.positionCS = TransformObjectToHClip(v.positionOS);
                o.uv = v.uv;
                o.color = v.color * unity_SpriteColor;
                return o;
            }

            half4 frag(Varyings i) : SV_Target
            {
                half4 c = SAMPLE_TEXTURE2D(_MainTex, sampler_MainTex, i.uv);
                half3 srgb = c.rgb;
            #if !defined(UNITY_COLORSPACE_GAMMA)
                srgb = LinearToSRGB(c.rgb);
            #endif
                float l = saturate(dot(srgb, half3(0.299, 0.587, 0.114)));
                float2 at = float2((l * 255.0 + 0.5) / 256.0, (_Row + 0.5) / max(_Rows, 1.0));
                half3 r = SAMPLE_TEXTURE2D(_Ramp, sampler_Ramp, at).rgb;
                return half4(lerp(c.rgb, r, _Strength), c.a) * i.color;
            }
            ENDHLSL
        }
    }
}
